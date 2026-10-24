import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { writeArrayBuffer } from 'geotiff';
import proj4 from 'proj4';
import { importSource, getSourcePixels, mapRequest, nativeWindowCount, tiffTransform, projectionForCode, pixelConverter, inversePixelConverter, validateBounds, fetchMapSource } from '../src/sources.js';

// Browser canvas/bitmap boundary adapter. TIFF bytes are genuinely encoded/decoded by geotiff.
// Native browser preview rendering is exercised separately in the browser acceptance run.
const originals = { OffscreenCanvas: globalThis.OffscreenCanvas, createImageBitmap: globalThis.createImageBitmap, fetch: globalThis.fetch };
let decoded;
class PixelCanvas {
  constructor(width, height) { this.width = width; this.height = height; this.data = new Uint8ClampedArray(width * height * 4); }
  getContext() { return { drawImage: image => { this.data.set(image.data); }, getImageData: () => ({ data: new Uint8ClampedArray(this.data) }), createImageData: (w, h) => ({ width: w, height: h, data: new Uint8ClampedArray(w * h * 4) }), putImageData: image => { this.data = image.data; } }; }
  async convertToBlob() { return new Blob([this.data], { type: 'image/png' }); }
}
beforeEach(() => {
  decoded = { width: 2, height: 2, data: new Uint8ClampedArray([3, 17, 251, 255, 44, 55, 66, 255, 77, 88, 99, 255, 101, 123, 145, 255]) };
  globalThis.OffscreenCanvas = PixelCanvas;
  globalThis.createImageBitmap = async () => ({ ...decoded, close() {} });
});
afterEach(() => { Object.assign(globalThis, originals); });
function tiffFile(extra = {}, values = new Uint8Array([3, 17, 251, 44, 55, 66, 77, 88, 99, 101, 123, 145])) {
  const bytes = writeArrayBuffer(values, { width: 2, height: 2, GeographicTypeGeoKey: 4326, GTModelTypeGeoKey: 2, GTRasterTypeGeoKey: 1, ModelPixelScale: [0.00001, 0.00001, 0], ModelTiepoint: [0, 0, 0, 88.4, 22.5, 0], ...extra });
  return new File([bytes], 'native.tif', { type: 'image/tiff' });
}
const near = (actual, expected, tolerance = 1e-7) => assert(Math.abs(actual - expected) < tolerance, `${actual} != ${expected}`);
const pngFile = () => new File([new Uint8Array([137, 80, 78, 71]), 'PNG decoder boundary adapter'], 'a.png', { type: 'image/png' });

test('real encoded RGB GeoTIFF keeps original bytes, dimensions, channel values and pixel georeferencing', async () => {
  const file = tiffFile(), source = await importSource(file);
  assert.equal(source.blob, file); assert.notEqual(source.previewBlob, file);
  assert.equal(source.metadata.resampled, false); assert.equal(source.metadata.raster_format, 'geotiff');
  assert.equal(source.metadata.kind, 'upload');
  assert.equal(source.metadata.acquisition_date, null);
  assert.match(source.metadata.attribution, /does not establish its provenance/);
  assert.deepEqual(new Uint8Array(await source.blob.arrayBuffer()), new Uint8Array(await file.arrayBuffer()));
  const decoded = await getSourcePixels(source);
  assert.equal(decoded.width, 2); assert.equal(decoded.height, 2);
  assert.deepEqual([...decoded.data], [3, 17, 251, 255, 44, 55, 66, 255, 77, 88, 99, 255, 101, 123, 145, 255]);
  near(decoded.pixelToLonLat(0, 0)[0], 88.4); near(decoded.pixelToLonLat(2, 2)[1], 22.49998);
  const coordinate = decoded.pixelToLonLat(1.37, 0.89), back = decoded.lonLatToPixel(...coordinate);
  near(back[0], 1.37); near(back[1], 0.89);
});

test('EPSG:3857 native TIFF is transformed rather than relabeled as WGS84', async () => {
  const origin = proj4('EPSG:4326', 'EPSG:3857', [88.4, 22.5]);
  const source = await importSource(tiffFile({ GeographicTypeGeoKey: undefined, ProjectedCSTypeGeoKey: 3857, GTModelTypeGeoKey: 1, ModelPixelScale: [0.3, 0.3, 0], ModelTiepoint: [0, 0, 0, ...origin, 0] }));
  assert.equal(source.metadata.crs, 'EPSG:3857');
  const point = (await getSourcePixels(source)).pixelToLonLat(0, 0); near(point[0], 88.4); near(point[1], 22.5);
});

test('WGS84 UTM north and south are supported; unknown and local CRSs are refused', () => {
  assert.equal(projectionForCode(32645), 'EPSG:32645'); assert.equal(projectionForCode(32755), 'EPSG:32755');
  const metadata = { crs: 'EPSG:32645', transform: [0.25, 0, 500000, 0, -0.25, 2500000] };
  const point = pixelConverter(metadata)(12.1, 20.3), back = inversePixelConverter(metadata)(...point); near(back[0], 12.1, 1e-5); near(back[1], 20.3, 1e-5);
  assert.throws(() => projectionForCode(27700), /CRS.*will not be guessed/);
  assert.throws(() => projectionForCode(NaN), /Unsupported GeoTIFF CRS/);
});

test('rotated affine and PixelIsPoint transforms round-trip without losing half-pixel offsets', () => {
  const transform = tiffTransform({ ModelTransformation: [2, 0.3, 0, 500000, -0.2, -2, 0, 2500000, 0, 0, 1, 0, 0, 0, 0, 1] }, { GTRasterTypeGeoKey: 1 });
  const metadata = { crs: 'EPSG:32645', transform };
  const point = pixelConverter(metadata)(0.5, 1.5), inverse = inversePixelConverter(metadata)(...point); near(inverse[0], 0.5); near(inverse[1], 1.5);
  assert.deepEqual(tiffTransform({ ModelPixelScale: [2, 4, 0], ModelTiepoint: [0, 0, 0, 100, 200, 0] }, { GTRasterTypeGeoKey: 2 }), [2, 0, 99, 0, -4, 202]);
  assert.throws(() => tiffTransform({}, {}), /georeferencing is missing/);
});

test('TIFF imports refuse unknown CRS and non-RGB data instead of guessing or stretching', async () => {
  await assert.rejects(importSource(tiffFile({ GeographicTypeGeoKey: 27700 })), /Unsupported GeoTIFF CRS/);
  await assert.rejects(importSource(tiffFile({ PhotometricInterpretation: 1, BitsPerSample: [8], SamplesPerPixel: 1, SampleFormat: [1] }, new Uint8Array([0, 50, 100, 255]))), /unsigned 8-bit RGB/);
});

test('GeoTIFF no-data and alpha pixels remain invalid for inference', async () => {
  const noData = await getSourcePixels(await importSource(tiffFile({ GDAL_NODATA: '0' }, new Uint8Array([0, 0, 0, 12, 22, 33, 44, 55, 66, 77, 88, 99]))));
  assert.equal(noData.data[3], 0); assert.equal(noData.data[7], 255); assert.equal(noData.data[4], 12);
  const alpha = await getSourcePixels(await importSource(tiffFile({ PhotometricInterpretation: 2, BitsPerSample: [8, 8, 8, 8], ExtraSamples: [2] }, new Uint8Array([3, 4, 5, 0, 6, 7, 8, 255, 9, 10, 11, 128, 12, 13, 14, 255]))));
  assert.equal(alpha.data[3], 0); assert.equal(alpha.data[11], 128); assert.equal(alpha.data[8], 9);
});

test('PNG/JPEG require explicit WGS84 bounds and keep decoded pixels at their original size', async () => {
  const file = pngFile();
  await assert.rejects(importSource(file), /Supply four finite WGS84 bounds/);
  const source = await importSource(file, [88.4, 22.5, 88.40002, 22.50002]);
  assert.equal(source.blob, file); assert.equal(source.metadata.bounds_supplied_by_user, true);
  assert.equal(source.metadata.kind, 'upload');
  assert.equal(source.metadata.acquisition_date, null);
  const pixels = await getSourcePixels(source);
  assert.deepEqual([...pixels.data], [...decoded.data]); assert.deepEqual(pixels.pixelToLonLat(2, 2), [88.40002, 22.5]);
});

test('size, native-window and invalid-bounds limits fail before silently resizing', async () => {
  assert.equal(nativeWindowCount(2048, 2048), 9); assert(nativeWindowCount(5000, 5000) > 16);
  for (const value of [[88, 22, 87, 23], [0, 86, 1, 87], [170, 0, -170, 1], [0, 0, Infinity, 1]]) assert.throws(() => validateBounds(value));
  await assert.rejects(importSource(new Blob([])), /non-empty image/);
  decoded.width = 5000; decoded.height = 5000;
  await assert.rejects(importSource(pngFile(), [88, 22, 89, 23]), /16 million pixels/);
  decoded.width = 3000; decoded.height = 3000;
  await assert.rejects(importSource(pngFile(), [88, 22, 89, 23]), /native inference windows/);
  await assert.rejects(importSource(new File(['<svg/>'], 'image.svg'), [88, 22, 89, 23]), /Unsupported image format/);
});

test('map request uses real Esri zoom-19 pixel spacing and refuses oversize AOIs', () => {
  const request = mapRequest([88.4, 22.5, 88.401, 22.501]), url = new URL(request.url);
  assert.equal(url.hostname, 'server.arcgisonline.com'); assert.equal(url.searchParams.get('f'), 'json');
  assert.equal(url.searchParams.get('bboxSR'), '3857'); assert.equal(url.searchParams.get('imageSR'), '3857');
  assert(request.width > 300 && request.width < 400); assert.equal(url.searchParams.get('size'), `${request.width},${request.height}`);
  assert.throws(() => mapRequest([88.4, 22.5, 88.5, 22.6]), /500 metres/);
});

test('map georeferencing uses returned provider extent and downloaded dimensions, with source attribution', async () => {
  const bounds = [88.4, 22.5, 88.401, 22.501], request = mapRequest(bounds), rectangle = new URL(request.url).searchParams.get('bbox').split(',').map(Number);
  decoded = { width: request.width, height: request.height, data: new Uint8ClampedArray(request.width * request.height * 4).fill(128) };
  const extent = { xmin: rectangle[0] - 0.12, ymin: rectangle[1], xmax: rectangle[2] + 0.12, ymax: rectangle[3], spatialReference: { wkid: 102100, latestWkid: 3857 } }, calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    if (calls.length === 1) return Response.json({ href: 'https://server.arcgisonline.com/arcgisoutput/actual.png', extent, width: request.width, height: request.height });
    return new Response(new Blob(['actual imagery response'], { type: 'image/png' }), { headers: { 'content-type': 'image/png' } });
  };
  const source = await fetchMapSource(bounds), pixels = await getSourcePixels(source);
  assert.equal(calls.length, 2); assert.equal(calls[0].options.mode, 'cors'); assert.equal(calls[0].options.credentials, 'omit');
  const originalCorner = proj4('EPSG:3857', 'EPSG:4326', [extent.xmin, extent.ymax]);
  near(pixels.pixelToLonLat(0, 0)[0], originalCorner[0]); near(pixels.pixelToLonLat(0, 0)[1], originalCorner[1]);
  assert.equal(source.rawRGBA.length, request.width * request.height * 4); assert.equal(source.metadata.map_zoom, 19);
  assert.equal(source.metadata.kind, 'map');
  assert.match(source.metadata.attribution, /Esri.*Maxar/); assert.equal(source.metadata.resampled, false);
});

test('map network, invalid export and dimension errors refuse detection rather than fake imagery', async () => {
  const bounds = [88.4, 22.5, 88.401, 22.501], request = mapRequest(bounds);
  globalThis.fetch = async () => { throw new Error('CORS failed'); };
  await assert.rejects(fetchMapSource(bounds), /CORS access/);
  globalThis.fetch = async () => Response.json({ href: 'https://untrusted.invalid/x.png', width: request.width, height: request.height });
  await assert.rejects(fetchMapSource(bounds), /invalid extent|CORS access/);
  const r = new URL(request.url).searchParams.get('bbox').split(',').map(Number);
  let count = 0;
  globalThis.fetch = async () => ++count === 1 ? Response.json({ href: 'https://server.arcgisonline.com/arcgisoutput/x.png', width: request.width, height: request.height, extent: { xmin: r[0], ymin: r[1], xmax: r[2], ymax: r[3], spatialReference: { wkid: 3857 } } }) : new Response(new Blob(['image']), { headers: { 'content-type': 'image/png' } });
  await assert.rejects(fetchMapSource(bounds), /unexpected dimensions/);
});
