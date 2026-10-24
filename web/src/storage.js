/** A single, on-device browser workspace. This is persistence, not cloud authentication. */
import { openDB } from 'idb';
import { measureGeometry } from './geometry.js';

const DEFAULT_DB = 'dronemap-browser-workspace-v1';
let connection;
let databaseName = DEFAULT_DB;
const previewURLs = new Map();

export class WorkspaceError extends Error {
  constructor(message, status = 422) { super(message); this.name = 'WorkspaceError'; this.status = status; }
}
const fail = (message, status = 422) => { throw new WorkspaceError(message, status); };
const timestamp = () => new Date().toISOString();
const identifier = () => crypto.randomUUID().replaceAll('-', '');
const clone = value => structuredClone(value);

export async function openStore(options = {}) {
  const name = typeof options === 'string' ? options : (options.name || DEFAULT_DB);
  if (connection && name !== databaseName) await closeStore();
  databaseName = name;
  if (!globalThis.indexedDB) fail('Browser storage is unavailable. Use a current browser with IndexedDB enabled.', 503);
  if (!connection) {
    connection = openDB(name, 1, {
      upgrade(db) {
        db.createObjectStore('projects', { keyPath: 'id' });
        const features = db.createObjectStore('features', { keyPath: 'id' });
        features.createIndex('project_id', 'properties.project_id');
        const sources = db.createObjectStore('sources', { keyPath: 'id' });
        sources.createIndex('project_id', 'metadata.project_id');
        const history = db.createObjectStore('history', { keyPath: 'key', autoIncrement: true });
        history.createIndex('feature_id', 'feature_id');
        db.createObjectStore('requests', { keyPath: ['project_id', 'request_key'] });
      },
      blocking() { closeStore(); },
      terminated() { connection = undefined; },
    }).catch(error => { connection = undefined; throw new WorkspaceError('Cannot open browser storage: ' + error.message, 503); });
  }
  return connection;
}

/** Close/reopen keeps saved data; it also releases temporary preview URLs. */
export async function closeStore() {
  if (connection) (await connection).close();
  connection = undefined;
  for (const url of previewURLs.values()) URL.revokeObjectURL(url);
  previewURLs.clear();
}

async function withWrite(names, callback) {
  const db = await openStore({ name: databaseName });
  const tx = db.transaction(names, 'readwrite');
  // Consume abort rejection even when the meaningful validation error is thrown first.
  const completion = tx.done;
  completion.catch(() => {});
  try {
    const result = await callback(tx);
    await completion;
    return result;
  } catch (error) {
    try { tx.abort(); } catch { /* transaction already finished/aborted */ }
    if (error?.name === 'QuotaExceededError') fail('Browser storage is full. Export your records and remove unused site data, or use the desktop edition.', 507);
    throw error;
  }
}

function projectFields(body) {
  if (!body || typeof body !== 'object') fail('Project details are required.');
  const result = {};
  for (const [key, limit] of [['name', 180], ['location', 300], ['description', 5000]]) {
    const value = body[key] ?? '';
    if (typeof value !== 'string' || value.length > limit) fail(`${key} must be text up to ${limit} characters.`);
    result[key] = value.trim();
  }
  if (!result.name) fail('Project name cannot be blank.');
  return result;
}

async function projectIn(tx, id) {
  const row = await tx.objectStore('projects').get(id);
  if (!row) fail('Project not found in this browser workspace.', 404);
  return row;
}
async function countedProject(row, db) {
  return { ...row, feature_count: await db.countFromIndex('features', 'project_id', row.id), source_count: await db.countFromIndex('sources', 'project_id', row.id) };
}
export async function listProjects() {
  const db = await openStore({ name: databaseName });
  const rows = await db.getAll('projects');
  return Promise.all(rows.sort((a, b) => b.updated_at.localeCompare(a.updated_at) || a.id.localeCompare(b.id)).map(row => countedProject(row, db)));
}
export async function getProject(id) {
  const db = await openStore({ name: databaseName }), row = await db.get('projects', id);
  if (!row) fail('Project not found in this browser workspace.', 404);
  return countedProject(row, db);
}
export async function createProject(body) {
  const fields = projectFields(body), now = timestamp();
  const project = { id: identifier(), ...fields, created_at: now, updated_at: now };
  await withWrite(['projects'], tx => tx.objectStore('projects').add(project));
  return { ...project, feature_count: 0, source_count: 0 };
}
export async function updateProject(id, body) {
  const fields = projectFields(body);
  await withWrite(['projects'], async tx => {
    const row = await projectIn(tx, id);
    await tx.objectStore('projects').put({ ...row, ...fields, updated_at: timestamp() });
  });
  return getProject(id);
}

function textProperty(properties, key, fallback, max) {
  const value = properties[key] ?? fallback;
  if (typeof value !== 'string' || value.length > max) fail(`${key} must be text up to ${max} characters.`);
  return value;
}
function finiteNumber(value, low, high, label, optional = true) {
  if (value == null && optional) return null;
  if (typeof value !== 'number' || !Number.isFinite(value) || value < low || value > high) fail(`${label} must be a finite number between ${low} and ${high}.`);
  return value;
}
function normalizedFeature(body) {
  if (!body || typeof body !== 'object') fail('Feature geometry is required.');
  const measured = measureGeometry(body.geometry);
  const p = body.properties ?? {};
  if (!p || typeof p !== 'object' || Array.isArray(p)) fail('Feature properties must be an object.');
  const feature_type = p.feature_type ?? 'building', status = p.status ?? 'draft';
  if (!['building', 'parcel', 'other'].includes(feature_type)) fail('Feature type must be building, parcel or other.');
  if (!['draft', 'reviewed'].includes(status)) fail('Feature status must be draft or reviewed.');
  const flags = p.quality_flags ?? [];
  if (!Array.isArray(flags) || flags.length > 3 || flags.some(f => !['inference_tile_edge', 'source_image_edge', 'scan_aoi_edge'].includes(f))) fail('Invalid feature quality flags.');
  const source_id = p.source_id ?? null;
  if (source_id !== null && (typeof source_id !== 'string' || source_id.length > 100)) fail('Invalid imagery source ID.');
  const props = {
    name: textProperty(p, 'name', 'Untitled feature', 200).trim() || 'Untitled feature',
    owner: textProperty(p, 'owner', '', 200), notes: textProperty(p, 'notes', '', 10000),
    reference_id: textProperty(p, 'reference_id', '', 200), feature_type, status, source_id,
    confidence: finiteNumber(p.confidence, 0, 1, 'Confidence'),
    mask_area_px: finiteNumber(p.mask_area_px, 0, Number.MAX_SAFE_INTEGER, 'Mask area'),
    quality_flags: [...new Set(flags)], touches_tile_edge: p.touches_tile_edge === true, touches_source_edge: p.touches_source_edge === true,
    area_sqm: measured.area_sqm, area_sqft: measured.area_sqft, perimeter_m: measured.perimeter_m,
    bbox: measured.bounds ?? measured.bbox, centroid: measured.centroid, vertex_count: measured.vertex_count,
  };
  return { geometry: measured.geometry, properties: props };
}
function stableJSON(value) {
  if (Array.isArray(value)) return '[' + value.map(stableJSON).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + stableJSON(value[key])).join(',') + '}';
  return JSON.stringify(value);
}
async function sourceInProject(tx, sourceId, projectId) {
  if (!sourceId) return;
  const source = await tx.objectStore('sources').get(sourceId);
  if (!source || source.metadata.project_id !== projectId) fail('Imagery source must belong to this project.');
}
async function recordHistory(tx, feature, action) {
  await tx.objectStore('history').add({ feature_id: feature.id, version: feature.properties.version, action, timestamp: timestamp(), user_name: 'This browser workspace', snapshot: clone(feature) });
}
function expectedVersion(value) {
  if (!Number.isSafeInteger(value) || value < 1) fail('A positive saved feature version is required.');
  return value;
}
export async function listFeatures(projectId) {
  await getProject(projectId);
  const db = await openStore({ name: databaseName });
  const features = await db.getAllFromIndex('features', 'project_id', projectId);
  features.sort((a, b) => a.properties.created_at.localeCompare(b.properties.created_at) || a.id.localeCompare(b.id));
  return { type: 'FeatureCollection', features };
}
export async function createFeature(projectId, body, idempotencyKey = null) {
  if (idempotencyKey != null && (typeof idempotencyKey !== 'string' || !/^[A-Za-z0-9_.:-]{1,128}$/.test(idempotencyKey))) fail('Invalid save key; use 1–128 letters, numbers, dots, underscores, colons or hyphens.');
  const normalized = normalizedFeature(body), payload = stableJSON(normalized);
  return withWrite(['projects', 'features', 'sources', 'history', 'requests'], async tx => {
    const project = await projectIn(tx, projectId), features = tx.objectStore('features');
    if (idempotencyKey) {
      const old = await tx.objectStore('requests').get([projectId, idempotencyKey]);
      if (old) {
        if (old.payload !== payload) fail('This save key was already used for different content. Reload before trying again.', 409);
        const saved = await features.get(old.feature_id);
        if (!saved) fail('This saved feature was deleted. Reload before creating another.', 409);
        return saved;
      }
    }
    await sourceInProject(tx, normalized.properties.source_id, projectId);
    const id = identifier(), now = timestamp();
    const feature = { type: 'Feature', id, geometry: normalized.geometry, properties: { ...normalized.properties, id, project_id: projectId, version: 1, created_at: now, updated_at: now, updated_by: 'This browser workspace', reviewed_by: normalized.properties.status === 'reviewed' ? 'This browser workspace' : null, reviewed_at: normalized.properties.status === 'reviewed' ? now : null } };
    await features.add(feature);
    await recordHistory(tx, feature, 'created');
    await tx.objectStore('projects').put({ ...project, updated_at: now });
    if (idempotencyKey) await tx.objectStore('requests').add({ project_id: projectId, request_key: idempotencyKey, payload, feature_id: id });
    return feature;
  });
}
export async function updateFeature(id, body) {
  const version = expectedVersion(body?.properties?.version), normalized = normalizedFeature(body);
  return withWrite(['projects', 'features', 'sources', 'history'], async tx => {
    const features = tx.objectStore('features'), row = await features.get(id);
    if (!row) fail('Feature not found.', 404);
    if (row.properties.version !== version) fail('This feature changed in another tab. Reload it before saving.', 409);
    const project = await projectIn(tx, row.properties.project_id);
    await sourceInProject(tx, normalized.properties.source_id, project.id);
    const now = timestamp();
    const feature = { type: 'Feature', id, geometry: normalized.geometry, properties: { ...normalized.properties, id, project_id: project.id, version: version + 1, created_at: row.properties.created_at, updated_at: now, updated_by: 'This browser workspace', reviewed_by: normalized.properties.status === 'reviewed' ? 'This browser workspace' : null, reviewed_at: normalized.properties.status === 'reviewed' ? now : null } };
    await features.put(feature);
    await recordHistory(tx, feature, 'updated');
    await tx.objectStore('projects').put({ ...project, updated_at: now });
    return feature;
  });
}
export async function deleteFeature(id, version) {
  expectedVersion(version);
  return withWrite(['projects', 'features', 'history'], async tx => {
    const features = tx.objectStore('features'), row = await features.get(id);
    if (!row) fail('Feature not found.', 404);
    if (row.properties.version !== version) fail('This feature changed in another tab. Reload it before deleting.', 409);
    const project = await projectIn(tx, row.properties.project_id);
    await recordHistory(tx, row, 'deleted');
    await features.delete(id);
    await tx.objectStore('projects').put({ ...project, updated_at: timestamp() });
    return { ok: true, id };
  });
}
export async function getFeature(id) {
  const db = await openStore({ name: databaseName }), feature = await db.get('features', id);
  if (!feature) fail('Feature not found.', 404);
  return feature;
}
export async function featureHistory(id) {
  const db = await openStore({ name: databaseName });
  const history = await db.getAllFromIndex('history', 'feature_id', id);
  if (!history.length) fail('Feature history not found.', 404);
  return { history: history.sort((a, b) => b.version - a.version || b.key - a.key).map(({ key, feature_id, ...row }) => row) };
}

function publicSource(record) {
  let preview_url = previewURLs.get(record.id);
  if (!preview_url) {
    preview_url = URL.createObjectURL(record.previewBlob || record.blob);
    previewURLs.set(record.id, preview_url);
  }
  return { ...clone(record.metadata), preview_url };
}
export async function listSources(projectId) {
  await getProject(projectId);
  const db = await openStore({ name: databaseName });
  return (await db.getAllFromIndex('sources', 'project_id', projectId)).sort((a, b) => b.metadata.created_at.localeCompare(a.metadata.created_at)).map(publicSource);
}
export async function addSource(projectId, source) {
  if (!source?.metadata || !(source.blob instanceof Blob) || !source.blob.size || source.blob.size > 32 * 1024 * 1024) fail('Choose an image up to 32 MB.');
  if (source.previewBlob && (!(source.previewBlob instanceof Blob) || !source.previewBlob.size || source.previewBlob.size > 32 * 1024 * 1024)) fail('Invalid imagery preview. Re-import the source.');
  const id = identifier(), now = timestamp();
  const record = { id, metadata: { ...clone(source.metadata), id, project_id: projectId, created_at: now }, blob: source.blob, previewBlob: source.previewBlob || source.blob };
  await withWrite(['projects', 'sources'], async tx => {
    const project = await projectIn(tx, projectId);
    await tx.objectStore('sources').add(record);
    await tx.objectStore('projects').put({ ...project, updated_at: now });
  });
  return publicSource(record);
}
export async function getSource(id, { includeBlob = false } = {}) {
  const db = await openStore({ name: databaseName }), record = await db.get('sources', id);
  if (!record) fail('Imagery source not found in this browser.', 404);
  return includeBlob ? record : publicSource(record);
}

export const WORKSPACE_NOTICE = 'Saved on this device in this browser only. No cloud account or cross-device sync. Export records regularly; clearing site data removes this workspace.';
