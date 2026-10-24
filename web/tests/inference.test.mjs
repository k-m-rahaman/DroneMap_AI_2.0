import test from 'node:test';
import assert from 'node:assert/strict';
import {windowPositions, planWindows, letterboxRGBA, boxIoU, decodeDetections, reconstructMask, maskOverlap, deduplicateMasks, maskPolygons, ringArea} from '../src/inference-core.js';
import {scanBuildings} from '../src/inference.js';

function mask(rows, x = 0, y = 0) {
  const data = Uint8Array.from(rows.flatMap(row => [...row].map(v => Number(v))));
  return {x, y, width: rows[0].length, height: rows.length, data, area: data.reduce((a, b) => a + b, 0)};
}
function predictions(items) {
  const count = items.length, data = new Float32Array(37 * count);
  items.forEach((item, i) => item.forEach((value, channel) => { data[channel * count + i] = value; }));
  return {data, dims: [1, 37, count]};
}
const square = (x, y, size = 2) => mask(Array(size).fill('1'.repeat(size)), x, y);
const rgba = (width, height) => ({width, height, data: new Uint8Array(width * height * 4).fill(255), pixelToLonLat: (x, y) => [88 + x / 100000, 22 - y / 100000]});

test('native windows cover the last pixel with overlap and no downsampling', () => {
  assert.deepEqual(windowPositions(768), [0]);
  assert.deepEqual(windowPositions(896), [0, 128]);
  assert.deepEqual(windowPositions(1500), [0, 640, 732]);
  assert.deepEqual(planWindows(896, 647).map(w => [w.x, w.y, w.width, w.height]), [[0, 0, 768, 647], [128, 0, 768, 647]]);
});
test('native window budget rejects large sources and accepts smaller AOIs', () => {
  assert.throws(() => planWindows(10000, 10000), /native-resolution windows/);
  assert.equal(planWindows(10000, 10000, [100, 200, 1100, 1200]).length, 4);
  assert.throws(() => planWindows(10, 10, [-1, 0, 5, 5]), /does not overlap/);
});
test('letterbox preserves source pixels, RGB channel order and native dimensions', () => {
  const source = rgba(2, 2); source.data.set([255, 64, 0, 255], 0);
  const result = letterboxRGBA(source, {x: 0, y: 0, width: 2, height: 2}), plane = 768 * 768, at = 383 * 768 + 383;
  assert.equal(result.padX, 383); assert.equal(result.padY, 383);
  assert.equal(result.data[at], 1); assert.ok(Math.abs(result.data[plane + at] - 64 / 255) < 1e-7); assert.equal(result.data[2 * plane + at], 0);
  assert.ok(Math.abs(result.data[0] - 114 / 255) < 1e-7);
});
test('transparent source pixels remain neutral padding instead of invisible RGB', () => {
  const source = rgba(2, 2); source.data.set([255, 0, 0, 0], 0);
  const result = letterboxRGBA(source, {x: 0, y: 0, width: 2, height: 2});
  assert.ok(Math.abs(result.data[383 * 768 + 383] - 114 / 255) < 1e-7);
  source.data[3] = 255; source.validMask = new Uint8Array([0, 1, 1, 1]);
  assert.ok(Math.abs(letterboxRGBA(source, {x: 0, y: 0, width: 2, height: 2}).data[383 * 768 + 383] - 114 / 255) < 1e-7);
});
test('one-class YOLO decode uses its score channel and suppresses same-instance duplicate boxes', () => {
  const decoded = decodeDetections(predictions([[10, 10, 8, 8, .8, 2], [10, 10, 8, 8, .9, 3], [30, 10, 8, 8, .7, 4], [100, 100, 5, 5, .2, 9]]), .5);
  assert.equal(decoded.length, 2); assert.equal(decoded[0].index, 1); assert.equal(decoded[0].coefficients[0], 3); assert.equal(decoded[1].index, 2);
});
test('decode rejects unsupported shapes, nonfinite boxes and nonfinite coefficients', () => {
  assert.throws(() => decodeDetections({dims: [1, 38, 3], data: new Float32Array(114)}), /unsupported prediction/);
  assert.equal(decodeDetections(predictions([[NaN, 4, 3, 3, .9], [4, 4, 3, 3, .9, Infinity]])).length, 0);
  assert.equal(boxIoU([0, 0, 2, 2], [3, 0, 5, 2]), 0);
});
test('mask prototype multiplication creates a real half-plane, then crops at the native detection box', () => {
  const source = rgba(768, 768), data = new Float32Array(32 * 192 * 192);
  for (let y = 0; y < 192; y++) for (let x = 0; x < 192; x++) data[y * 192 + x] = x < 96 ? 1 : -1;
  const detection = {box: [380, 0, 388, 8], coefficients: Float32Array.from({length: 32}, (_, i) => i === 0 ? 1 : 0)};
  const result = reconstructMask({dims: [1, 32, 192, 192], data}, detection, {width: 768, height: 768, padX: 0, padY: 0}, {x: 0, y: 0}, source);
  assert.equal(result.area, 32); assert.equal(result.width, 8); assert.equal(result.height, 8);
  assert.deepEqual([...result.data.slice(0, 8)], [1, 1, 1, 1, 0, 0, 0, 0]);
  assert.equal(result.invalidData, false);
});
test('retina reconstruction removes letterbox without scaling original source pixels', () => {
  const source = rgba(12, 8), data = new Float32Array(32 * 192 * 192); data.fill(1, 0, 192 * 192);
  const coefficients = new Float32Array(32); coefficients[0] = 1;
  const result = reconstructMask({dims: [1, 32, 192, 192], data}, {box: [378, 380, 390, 388], coefficients}, {width: 12, height: 8, padX: 378, padY: 380}, {x: 0, y: 0}, source);
  assert.equal(result.area, 96); assert.equal(result.x, 0); assert.equal(result.y, 0);
  source.data[3] = 0;
  assert.equal(reconstructMask({dims: [1, 32, 192, 192], data}, {box: [378, 380, 390, 388], coefficients}, {width: 12, height: 8, padX: 378, padY: 380}, {x: 0, y: 0}, source).invalidData, true);
});
test('small tiles use the recorded padding inverse, without an invented upscale gain', () => {
  const source = rgba(12, 8), data = new Float32Array(32 * 192 * 192), coefficients = new Float32Array(32); coefficients[0] = 1;
  for (let y = 0; y < 192; y++) for (let x = 0; x < 192; x++) data[y * 192 + x] = x < 95 ? 1 : -1;
  const result = reconstructMask({dims: [1, 32, 192, 192], data}, {box: [378, 380, 390, 388], coefficients}, {width: 12, height: 8, padX: 378, padY: 380}, {x: 0, y: 0}, source);
  assert.equal(result.area, 16);
  assert.deepEqual([...result.data.slice(0, 12)], [1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
});
test('foreground touching an explicit nodata mask is flagged for rejection', () => {
  const source = rgba(2, 2); source.validMask = new Uint8Array([0, 1, 1, 1]);
  const data = new Float32Array(32 * 192 * 192); data.fill(1, 0, 192 * 192);
  const coefficients = new Float32Array(32); coefficients[0] = 1;
  const result = reconstructMask({dims: [1, 32, 192, 192], data}, {box: [383, 383, 385, 385], coefficients}, {width: 2, height: 2, padX: 383, padY: 383}, {x: 0, y: 0}, source);
  assert.equal(result.invalidData, true);
});
test('mask contours retain a concave footprint rather than its bounding box', () => {
  const shape = mask(['101', '101', '111'], 10, 20), polygons = maskPolygons(shape);
  assert.equal(polygons.length, 1); assert.equal(polygons[0][0].length, 9);
  assert.equal(ringArea(polygons[0][0]), 7); assert.deepEqual(polygons[0][0][0], polygons[0][0].at(-1));
});
test('mask contours preserve holes and distinguish diagonal neighbours', () => {
  const donut = maskPolygons(mask(['111', '101', '111']));
  assert.equal(donut.length, 1); assert.equal(donut[0].length, 2); assert.equal(donut[0].reduce((a, r) => a + ringArea(r), 0), 8);
  const diagonal = maskPolygons(mask(['10', '01']));
  assert.equal(diagonal.length, 2); assert.ok(diagonal.every(p => ringArea(p[0]) === 1));
});
test('pixel-boundary contour areas exactly preserve deterministic irregular binary masks', () => {
  let seed = 1234;
  for (let k = 0; k < 100; k++) {
    const rows = Array.from({length: 8}, () => Array.from({length: 8}, () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed >>> 31; }).join(''));
    const source = mask(rows), area = maskPolygons(source).reduce((a, polygon) => a + polygon.reduce((b, ring) => b + ringArea(ring), 0), 0);
    assert.equal(area, source.area, `mask ${k}`);
  }
});
test('overlap is calculated from actual mask pixels, not box overlap', () => {
  const left = mask(['10', '10']), right = mask(['01', '01']);
  assert.equal(maskOverlap(left, right).intersection, 0);
  assert.equal(deduplicateMasks([{mask: left, confidence: .9, truncated: false}, {mask: right, confidence: .9, truncated: false}]).length, 2);
});
test('overlapping native windows remove duplicate masks and favour complete roofs', () => {
  const complete = {mask: square(100, 100, 4), confidence: .6, truncated: false};
  const cut = {mask: square(101, 101, 2), confidence: .95, truncated: true};
  assert.equal(maskOverlap(complete.mask, cut.mask).intersectionOverMinimum, 1);
  assert.deepEqual(deduplicateMasks([cut, complete]), [complete]);
  assert.equal(deduplicateMasks([complete, {mask: square(104, 100, 4), confidence: .8, truncated: false}]).length, 2);
});
test('empty segmentation masks have no fabricated polygon', () => {
  assert.deepEqual(maskPolygons(mask(['000', '000'])), []);
});
test('scan rejects malformed inputs and settings before loading the model', async () => {
  await assert.rejects(scanBuildings({width: 2, height: 2}), /georeferenced RGBA/);
  await assert.rejects(scanBuildings(rgba(2, 2), {confidence: NaN}), /Confidence/);
  await assert.rejects(scanBuildings(rgba(2, 2), {minAreaSqm: -1}), /Minimum/);
  await assert.rejects(scanBuildings(rgba(2, 2), {bounds: [3, 2, 1, 4]}), /valid WGS84/);
});
test('cancelled scan stops before model fetch and never returns synthetic features', async () => {
  const controller = new AbortController(); controller.abort();
  await assert.rejects(scanBuildings(rgba(2, 2), {signal: controller.signal}), {name: 'AbortError'});
});
