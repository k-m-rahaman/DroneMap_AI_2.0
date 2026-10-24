import {measureGeometry} from './geometry.js';
import {TILE_SIZE, TILE_OVERLAP, planWindows, letterboxRGBA, decodeDetections, reconstructMask, deduplicateMasks, maskPolygons, ringArea} from './inference-core.js';

let runtimePromise, sessionPromise, sessionURL, active = false;
const abortIfNeeded = signal => { if (signal?.aborted) throw new DOMException('Building detection was cancelled.', 'AbortError'); };
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
const clock = () => globalThis.performance?.now() ?? Date.now();

function validateSource(source) {
  if (!source || !Number.isInteger(source.width) || !Number.isInteger(source.height) || source.width < 2 || source.height < 2 || !ArrayBuffer.isView(source.data) || source.data.length !== source.width * source.height * 4 || typeof source.pixelToLonLat !== 'function') throw new Error('Load a georeferenced RGBA image before building detection.');
  if (source.validMask && source.validMask.length !== source.width * source.height) throw new Error('The imagery valid-data mask has an incorrect size.');
}

function validateBounds(bounds) {
  if (!Array.isArray(bounds) || bounds.length !== 4 || !bounds.every(Number.isFinite) || bounds[0] >= bounds[2] || bounds[1] >= bounds[3] || bounds[0] < -180 || bounds[2] > 180 || bounds[1] < -85.051129 || bounds[3] > 85.051129) throw new Error('Choose a valid WGS84 scan area without crossing the date line.');
  return bounds;
}

function scanRegion(source, bounds) {
  if (!bounds) return [0, 0, source.width, source.height];
  const inverse = source.lonLatToPixel || source.pixelFromLonLat;
  if (!inverse) return [0, 0, source.width, source.height];
  const points = [];
  for (let i = 0; i <= 16; i++) {
    const fraction = i / 16, lon = bounds[0] + (bounds[2] - bounds[0]) * fraction, lat = bounds[1] + (bounds[3] - bounds[1]) * fraction;
    points.push(inverse(lon, bounds[1]), inverse(lon, bounds[3]), inverse(bounds[0], lat), inverse(bounds[2], lat));
  }
  if (!points.every(point => Array.isArray(point) && point.length >= 2 && point.slice(0, 2).every(Number.isFinite))) throw new Error('The selected area cannot be transformed to source pixels.');
  return [Math.max(0, Math.floor(Math.min(...points.map(point => point[0]))) - 2), Math.max(0, Math.floor(Math.min(...points.map(point => point[1]))) - 2), Math.min(source.width, Math.ceil(Math.max(...points.map(point => point[0]))) + 2), Math.min(source.height, Math.ceil(Math.max(...points.map(point => point[1]))) + 2)];
}

async function loadModel(options, progress) {
  const modelURL = String(options.modelUrl || new URL('models/buildings.onnx', globalThis.document?.baseURI || import.meta.url));
  const wasmBase = String(options.wasmBaseUrl || new URL('runtime/', globalThis.document?.baseURI || import.meta.url));
  if (sessionPromise && sessionURL !== modelURL) {
    const previous = await sessionPromise;
    await previous.release(); sessionPromise = null;
  }
  if (!runtimePromise) runtimePromise = import('onnxruntime-web/wasm');
  const ort = await runtimePromise;
  ort.env.wasm.numThreads = 1;
  ort.env.wasm.proxy = false;
  ort.env.wasm.wasmPaths = wasmBase.endsWith('/') ? wasmBase : `${wasmBase}/`;
  if (!sessionPromise) {
    sessionURL = modelURL;
    sessionPromise = (async () => {
      progress({stage: 'loading', message: 'Loading the building segmentation model into your browser…', completed: 0, total: 1, progress: 0});
      const response = await fetch(modelURL, {signal: options.signal});
      if (!response.ok) throw new Error(`The building model could not be downloaded (HTTP ${response.status}). Retry when connected.`);
      if (Number(response.headers.get('content-length') || 0) > 32 * 1024 * 1024) throw new Error('The building model exceeds the supported download size.');
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (bytes.length === 0 || bytes.length > 32 * 1024 * 1024) throw new Error('The building model download is empty or too large.');
      abortIfNeeded(options.signal);
      return ort.InferenceSession.create(bytes, {executionProviders: ['wasm'], graphOptimizationLevel: 'all'});
    })().catch(error => { sessionPromise = null; throw error; });
  }
  return {ort, session: await sessionPromise};
}

/** Real local segmentation in ONNX Runtime Web. No server, uploaded imagery or simulated detections. */
export async function scanBuildings(source, options = {}) {
  if (active) throw new Error('A building scan is already running. Cancel it or wait for it to finish.');
  validateSource(source);
  const confidence = options.confidence ?? 0.5, minAreaSqm = options.minAreaSqm ?? 4;
  if (!Number.isFinite(confidence) || confidence < 0.05 || confidence > 0.95) throw new Error('Confidence must be between 0.05 and 0.95.');
  if (!Number.isFinite(minAreaSqm) || minAreaSqm < 0 || minAreaSqm > 10000) throw new Error('Minimum building area must be between 0 and 10,000 square metres.');
  const bounds = options.bounds == null ? null : validateBounds(options.bounds);
  const region = scanRegion(source, bounds), windows = planWindows(source.width, source.height, region);
  const progress = typeof options.onProgress === 'function' ? options.onProgress : () => {};
  const started = clock(), candidates = [];
  let processed = 0, empty = 0, invalidData = 0, smallPixels = 0, invalidGeometry = 0, smallArea = 0;
  active = true;
  try {
    abortIfNeeded(options.signal);
    const {ort, session} = await loadModel(options, progress);
    abortIfNeeded(options.signal);
    for (const tile of windows) {
      abortIfNeeded(options.signal);
      progress({stage: 'inference', message: `Detecting buildings in native window ${processed + empty + 1} of ${windows.length}…`, completed: processed + empty, total: windows.length, progress: (processed + empty) / windows.length});
      await tick();
      let hasValid = false;
      for (let y = tile.y; y < tile.y + tile.height && !hasValid; y++) for (let x = tile.x; x < tile.x + tile.width; x++) {
        const index = y * source.width + x;
        if (source.data[index * 4 + 3] !== 0 && (!source.validMask || source.validMask[index])) { hasValid = true; break; }
      }
      if (!hasValid) { empty++; continue; }
      const letterbox = letterboxRGBA(source, tile), tensor = new ort.Tensor('float32', letterbox.data, [1, 3, TILE_SIZE, TILE_SIZE]);
      let outputs;
      try {
        outputs = await session.run({[session.inputNames[0]]: tensor});
        abortIfNeeded(options.signal);
        const prediction = Object.values(outputs).find(output => output.dims.length === 3), prototypes = Object.values(outputs).find(output => output.dims.length === 4);
        if (!prediction || !prototypes) throw new Error('The building model did not return segmentation masks.');
        for (const detection of decodeDetections(prediction, confidence)) {
          const mask = reconstructMask(prototypes, detection, letterbox, tile, source);
          if (!mask || mask.area < 4) { smallPixels++; continue; }
          if (mask.invalidData) { invalidData++; continue; }
          // Determine real foreground bounds rather than the wider detection box.
          let minX = mask.width, minY = mask.height, maxX = 0, maxY = 0;
          for (let y = 0; y < mask.height; y++) for (let x = 0; x < mask.width; x++) if (mask.data[y * mask.width + x]) { minX = Math.min(minX, x); minY = Math.min(minY, y); maxX = Math.max(maxX, x + 1); maxY = Math.max(maxY, y + 1); }
          const extent = [mask.x + minX, mask.y + minY, mask.x + maxX, mask.y + maxY];
          const truncated = (tile.x > region[0] && extent[0] <= tile.x + 2) || (tile.y > region[1] && extent[1] <= tile.y + 2) || (tile.x + tile.width < region[2] && extent[2] >= tile.x + tile.width - 2) || (tile.y + tile.height < region[3] && extent[3] >= tile.y + tile.height - 2);
          const sourceEdge = extent[0] <= 1 || extent[1] <= 1 || extent[2] >= source.width - 1 || extent[3] >= source.height - 1;
          candidates.push({mask, confidence: detection.confidence, truncated, sourceEdge});
          if (candidates.length > 2500) throw new Error('More than 2,500 mask candidates were found. Select a smaller scan area.');
        }
      } finally {
        tensor.dispose?.();
        for (const output of Object.values(outputs || {})) output.dispose?.();
      }
      processed++;
      await tick();
    }
    abortIfNeeded(options.signal);
    progress({stage: 'polygons', message: 'Converting segmentation masks into measured building polygons…', completed: windows.length, total: windows.length, progress: 1});
    await tick();
    const unique = deduplicateMasks(candidates), features = [];
    const turf = bounds ? await import('@turf/turf') : null;
    const clip = bounds ? turf.bboxPolygon(bounds) : null;
    for (let i = 0; i < unique.length; i++) {
      abortIfNeeded(options.signal);
      const candidate = unique[i];
      let polygons;
      try { polygons = maskPolygons(candidate.mask); } catch { invalidGeometry++; continue; }
      for (const pixelRings of polygons) {
        const pixelArea = pixelRings.reduce((sum, ring) => sum + ringArea(ring), 0);
        if (pixelArea < 4) { smallPixels++; continue; }
        let geometry = {type: 'Polygon', coordinates: pixelRings.map(ring => ring.map(point => source.pixelToLonLat(...point).slice(0, 2)))};
        let aoiEdge = false;
        try {
          if (clip) {
            const clipped = turf.intersect(turf.featureCollection([turf.feature(geometry), clip]));
            if (!clipped) continue;
            geometry = clipped.geometry;
            const rings = geometry.type === 'MultiPolygon' ? geometry.coordinates.flat() : geometry.coordinates;
            aoiEdge = rings.some(ring => ring.some(([lon, lat]) => Math.min(Math.abs(lon - bounds[0]), Math.abs(lon - bounds[2]), Math.abs(lat - bounds[1]), Math.abs(lat - bounds[3])) < 1e-9));
          }
          const pieces = geometry.type === 'MultiPolygon' ? geometry.coordinates : [geometry.coordinates];
          for (const coordinates of pieces) {
            const measured = measureGeometry({type: 'Polygon', coordinates});
            if (measured.area_sqm < minAreaSqm) { smallArea++; continue; }
            const flags = [...(candidate.truncated ? ['inference_tile_edge'] : []), ...(candidate.sourceEdge ? ['source_image_edge'] : []), ...(aoiEdge ? ['scan_aoi_edge'] : [])];
            features.push({type: 'Feature', id: `proposal-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${i}-${features.length}`}`, geometry: measured.geometry, properties: {
              name: 'Building footprint', feature_type: 'building', status: 'draft', source_id: source.id ?? null, confidence: Number(candidate.confidence.toFixed(5)),
              area_sqm: measured.area_sqm, area_sqft: measured.area_sqft, perimeter_m: measured.perimeter_m, mask_area_px: pixelArea,
              quality_flags: flags, needs_review: true, touches_tile_edge: candidate.truncated, touches_source_edge: candidate.sourceEdge,
              notes: 'AI building proposal. Review against source imagery and field observations; this is not an ownership parcel.'
            }});
            if (features.length > 1000) throw new Error('More than 1,000 building proposals were found. Split this survey into smaller areas.');
          }
        } catch (error) {
          if (features.length > 1000) throw error;
          invalidGeometry++;
        }
      }
      if (i % 10 === 0) await tick();
    }
    const limitations = [
      'Building segmentation only; property ownership, cadastral parcels, roads and land use are not inferred.',
      'All proposals require visual review. Model accuracy has not been independently measured on this imagery.',
      'Native source pixels are padded, never reduced for inference; the fixed square ONNX input can differ from the Python rectangular predictor.',
      'Browser data stays on this device. Back up your projects before clearing site data.',
      'Masks touching transparent or invalid source pixels are discarded. Small real structures may be excluded by the area filter.'
    ];
    const warnings = [];
    if (invalidGeometry) warnings.push(`${invalidGeometry} mask geometries could not be measured safely and were skipped.`);
    if (invalidData) warnings.push(`${invalidData} masks touched transparent or invalid imagery and were skipped.`);
    if (features.some(feature => feature.properties.quality_flags.length)) warnings.push('Some footprints meet a scan or image edge and may be incomplete. Inspect their quality flags.');
    progress({stage: 'completed', message: `${features.length} building proposals ready for review.`, completed: windows.length, total: windows.length, progress: 1});
    return {geojson: {type: 'FeatureCollection', features}, inference: {
      state: 'completed', model: 'buildings.onnx', classes: ['building'], device: 'browser-wasm-cpu', confidence_threshold: confidence,
      tile_size: TILE_SIZE, tile_overlap: TILE_OVERLAP, tiles_processed: processed, tiles_planned: windows.length,
      empty_nodata_tiles_skipped: empty, nodata_masks_removed: invalidData, raw_candidates: candidates.length, native_resolution: true, analysis_resampled: false,
      scan_bounds: bounds, scan_window_px: region, minimum_mask_area_px: 4, small_pixel_masks_removed: smallPixels,
      minimum_area_sqm: minAreaSqm, small_area_masks_removed: smallArea, duplicate_masks_removed: candidates.length - unique.length,
      invalid_geometries_skipped: invalidGeometry, detections: features.length, elapsed_seconds: Number(((clock() - started) / 1000).toFixed(2)), limitations
    }, warnings};
  } catch (error) {
    if (error?.name === 'AbortError') throw error;
    throw new Error(`Building detection failed: ${error?.message || error}. No simulated detections were substituted.`, {cause: error});
  } finally { active = false; }
}

export const detectBuildings = scanBuildings;
export const runInference = scanBuildings;
