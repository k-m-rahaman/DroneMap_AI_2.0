import 'fake-indexeddb/auto';
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import * as store from '../src/storage.js';

let dbName;
beforeEach(async () => { dbName = `dronemap-test-${crypto.randomUUID()}`; await store.openStore({ name: dbName }); });
afterEach(async () => { await store.closeStore(); await new Promise((resolve, reject) => { const request = indexedDB.deleteDatabase(dbName); request.onsuccess = resolve; request.onerror = () => reject(request.error); }); });
const feature = (extra = {}) => ({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [[[88.4, 22.5], [88.4001, 22.5], [88.4001, 22.5001], [88.4, 22.5001], [88.4, 22.5]]] }, properties: { name: 'Roof A', feature_type: 'building', ...extra } });
const conflict = error => error.status === 409;

test('saved projects, actual geometry and measured values survive a database close/reopen', async () => {
  const project = await store.createProject({ name: '  SIH survey  ', location: 'Kolkata', description: 'Saved locally' });
  const saved = await store.createFeature(project.id, feature({ owner: 'Entered by reviewer', area_sqm: 999999, status: 'reviewed' }));
  assert.equal(saved.properties.version, 1); assert(saved.properties.area_sqm > 90 && saved.properties.area_sqm < 120);
  assert(saved.properties.area_sqft > 900); assert.equal(saved.properties.vertex_count, 5);
  assert.equal(saved.properties.bbox.length, 4); assert.equal(saved.properties.reviewed_by, 'This browser workspace');
  await store.closeStore(); await store.openStore(dbName);
  assert.equal((await store.getProject(project.id)).name, 'SIH survey');
  const collection = await store.listFeatures(project.id);
  assert.equal(collection.type, 'FeatureCollection'); assert.deepEqual(collection.features[0], saved);
  assert.equal((await store.listProjects())[0].feature_count, 1);
});

test('retrying the same save key creates one feature and one history record, including after reopen', async () => {
  const project = await store.createProject({ name: 'Retries' });
  const first = await store.createFeature(project.id, feature(), 'save:123');
  const retried = await store.createFeature(project.id, feature(), 'save:123');
  assert.equal(retried.id, first.id);
  await store.closeStore(); await store.openStore(dbName);
  assert.equal((await store.createFeature(project.id, feature(), 'save:123')).id, first.id);
  assert.equal((await store.listFeatures(project.id)).features.length, 1);
  assert.equal((await store.featureHistory(first.id)).history.length, 1);
  await assert.rejects(store.createFeature(project.id, feature({ name: 'Different payload' }), 'save:123'), conflict);
  assert.equal((await store.listFeatures(project.id)).features.length, 1);
});

test('an actual boundary edit is persisted and stale edit/delete requests cannot overwrite it', async () => {
  const project = await store.createProject({ name: 'Review' }), saved = await store.createFeature(project.id, feature());
  const change = feature({ ...saved.properties, status: 'reviewed' });
  change.geometry.coordinates[0][1][0] += 0.00002;
  const edited = await store.updateFeature(saved.id, change);
  assert.notDeepEqual(edited.geometry, saved.geometry); assert(edited.properties.area_sqm > saved.properties.area_sqm);
  assert.equal(edited.properties.version, 2);
  await assert.rejects(store.updateFeature(saved.id, feature({ ...saved.properties, name: 'Stale write' })), conflict);
  await assert.rejects(store.deleteFeature(saved.id, 1), conflict);
  await store.closeStore(); await store.openStore(dbName);
  assert.deepEqual((await store.getFeature(saved.id)).geometry, edited.geometry);
  const history = (await store.featureHistory(saved.id)).history;
  assert.equal(history.length, 2); assert.deepEqual(history[0].snapshot.geometry, edited.geometry);
  assert.deepEqual(history[1].snapshot.geometry, saved.geometry);
  await store.deleteFeature(saved.id, 2);
  assert.equal((await store.listFeatures(project.id)).features.length, 0);
  assert.equal((await store.featureHistory(saved.id)).history[0].action, 'deleted');
});

test('a save key cannot resurrect a deleted feature', async () => {
  const project = await store.createProject({ name: 'Deleted retry' }), saved = await store.createFeature(project.id, feature(), 'request-one');
  await store.deleteFeature(saved.id, 1);
  await assert.rejects(store.createFeature(project.id, feature(), 'request-one'), conflict);
});

test('sources retain original binary bytes and a separate preview across reopen', async () => {
  const project = await store.createProject({ name: 'Imagery' });
  const bytes = new Uint8Array([73, 73, 42, 0, 7, 255, 9, 11]);
  const original = new Blob([bytes], { type: 'image/tiff' }), preview = new Blob(['preview-only'], { type: 'image/png' });
  const source = await store.addSource(project.id, { metadata: { name: 'Native RGB orthomosaic', width: 2, height: 2, bounds: [88, 22, 89, 23], crs: 'EPSG:4326', transform: [0.5, 0, 88, 0, -0.5, 23] }, blob: original, previewBlob: preview });
  assert(source.preview_url.startsWith('blob:')); assert.equal(source.project_id, project.id);
  await store.closeStore(); await store.openStore(dbName);
  const record = await store.getSource(source.id, { includeBlob: true });
  assert(record.blob instanceof Blob); assert.deepEqual(new Uint8Array(await record.blob.arrayBuffer()), bytes);
  assert.equal(record.blob.type, 'image/tiff'); assert.equal(await record.previewBlob.text(), 'preview-only');
  const reopened = (await store.listSources(project.id))[0];
  assert(reopened.preview_url.startsWith('blob:')); assert.notEqual(reopened.preview_url, source.preview_url);
  assert.equal((await store.getProject(project.id)).source_count, 1);
});

test('features can reference only imagery belonging to their project', async () => {
  const one = await store.createProject({ name: 'One' }), two = await store.createProject({ name: 'Two' });
  const source = await store.addSource(one.id, { metadata: { name: 'Map source' }, blob: new Blob(['real bytes']) });
  await assert.rejects(store.createFeature(two.id, feature({ source_id: source.id })), /belong to this project/);
  const saved = await store.createFeature(one.id, feature({ source_id: source.id }));
  assert.equal(saved.properties.source_id, source.id);
  assert.equal((await store.listFeatures(two.id)).features.length, 0);
});

test('validation failure does not leave partial project, feature or history writes', async () => {
  await assert.rejects(store.createProject({ name: ' ' }), /cannot be blank/);
  assert.equal((await store.listProjects()).length, 0);
  const project = await store.createProject({ name: 'Validation' });
  await assert.rejects(store.createFeature(project.id, feature(), 123), /Invalid save key/);
  await assert.rejects(store.createFeature(project.id, feature({ confidence: 1.1 })), /Confidence/);
  await assert.rejects(store.createFeature(project.id, feature({ feature_type: 'owner' })), /Feature type/);
  await assert.rejects(store.createFeature(project.id, feature({ source_id: 'missing' })), /belong to this project/);
  assert.equal((await store.listFeatures(project.id)).features.length, 0);
});
