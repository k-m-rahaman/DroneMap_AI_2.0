/** Real, native-resolution imagery in the browser. Preview resampling never changes inference pixels. */
import { fromArrayBuffer } from 'geotiff';
import proj4 from 'proj4';

export const SOURCE_LIMITS = Object.freeze({ bytes: 32 * 1024 * 1024, pixels: 16_000_000, windows: 16, mapSideMetres: 500, mapEdgePixels: 2048, tileSize: 768, tileOverlap: 128 });
const WEB_MERCATOR_EDGE = 20037508.342789244;
const MAP_SERVICE = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export';
const ATTRIBUTION = 'Esri World Imagery; Esri, Maxar, Earthstar Geographics and the GIS User Community. Source imagery dates and positional accuracy vary.';
const finite = value => typeof value === 'number' && Number.isFinite(value);
const fail = message => { const error = new Error(message); error.status = 422; throw error; };

export function validateBounds(value) {
  if (!Array.isArray(value) || value.length !== 4 || !value.every(finite)) fail('Supply four finite WGS84 bounds: west, south, east, north.');
  const [w, s, e, n] = value;
  if (w < -180 || e > 180 || s < -85.05112878 || n > 85.05112878 || w >= e || s >= n || e - w > 180) fail('Bounds must describe a non-empty area within Web Mercator coverage, without crossing the date line.');
  return [...value];
}
function checkDimensions(width, height) {
  if (![width, height].every(n => Number.isSafeInteger(n) && n >= 2) || width * height > SOURCE_LIMITS.pixels) fail('Use imagery with at least 2 pixels per side and at most 16 million pixels.');
  const count = nativeWindowCount(width, height);
  if (count > SOURCE_LIMITS.windows) fail(`This image requires ${count} native inference windows; the browser limit is 16. Crop the image with GIS software first. It will not be silently downsampled.`);
}
export function nativeWindowCount(width, height) {
  const axis = edge => edge <= SOURCE_LIMITS.tileSize ? 1 : Math.ceil((edge - SOURCE_LIMITS.tileSize) / (SOURCE_LIMITS.tileSize - SOURCE_LIMITS.tileOverlap)) + 1;
  return axis(width) * axis(height);
}
function mercator([lon, lat]) { return proj4('EPSG:4326', 'EPSG:3857', [lon, lat]); }
export function projectionForCode(code) {
  if (code === 4326 || code === 3857) return `EPSG:${code}`;
  if (Number.isInteger(code) && ((code >= 32601 && code <= 32660) || (code >= 32701 && code <= 32760))) {
    const zone = code % 100, south = code >= 32700;
    const name = `EPSG:${code}`;
    if (!proj4.defs(name)) proj4.defs(name, `+proj=utm +zone=${zone} ${south ? '+south ' : ''}+datum=WGS84 +units=m +no_defs`);
    return name;
  }
  fail(`Unsupported GeoTIFF CRS${code ? ` EPSG:${code}` : ''}. Reproject to EPSG:4326, EPSG:3857 or WGS84 UTM using GIS software. The CRS will not be guessed.`);
}
function validateTransform(transform) {
  if (!Array.isArray(transform) || transform.length !== 6 || !transform.every(finite) || Math.abs(transform[0] * transform[4] - transform[1] * transform[3]) < 1e-20) fail('The image has no valid, invertible georeferencing transform.');
  return transform;
}
export function tiffTransform(directory, keys) {
  if (![1, 2].includes(Number(keys.GTRasterTypeGeoKey || 1))) fail('Unsupported GeoTIFF raster pixel interpretation.');
  let transform;
  const matrix = directory.ModelTransformation;
  if (matrix) {
    if (matrix.length !== 16 || !Array.from(matrix).every(finite) || Math.abs(matrix[12]) > 1e-12 || Math.abs(matrix[13]) > 1e-12 || matrix[15] !== 1) fail('Unsupported non-affine GeoTIFF transformation.');
    transform = [matrix[0], matrix[1], matrix[3], matrix[4], matrix[5], matrix[7]];
  } else {
    const scale = directory.ModelPixelScale, tie = directory.ModelTiepoint;
    if (!scale || !tie || scale.length < 2 || tie.length < 6 || scale[0] <= 0 || scale[1] <= 0) fail('GeoTIFF georeferencing is missing. Supply a georeferenced orthomosaic.');
    transform = [scale[0], 0, tie[3] - tie[0] * scale[0], 0, -scale[1], tie[4] + tie[1] * scale[1]];
  }
  if (Number(keys.GTRasterTypeGeoKey || 1) === 2) {
    transform[2] -= (transform[0] + transform[1]) / 2;
    transform[5] -= (transform[3] + transform[4]) / 2;
  }
  return validateTransform(transform);
}
export function pixelConverter(metadata) {
  const [a, b, c, d, e, f] = validateTransform(metadata.transform), crs = projectionForCode(Number(String(metadata.crs).replace('EPSG:', '')));
  return (x, y) => proj4(crs, 'EPSG:4326', [a * x + b * y + c, d * x + e * y + f]);
}
export function inversePixelConverter(metadata) {
  const [a, b, c, d, e, f] = validateTransform(metadata.transform), determinant = a * e - b * d;
  const crs = projectionForCode(Number(String(metadata.crs).replace('EPSG:', '')));
  return (lon, lat) => {
    const [x, y] = proj4('EPSG:4326', crs, [lon, lat]);
    return [(e * (x - c) - b * (y - f)) / determinant, (-d * (x - c) + a * (y - f)) / determinant];
  };
}
function sourceBounds(width, height, pixelToLonLat) {
  const edge = [];
  for (let i = 0; i <= 32; i++) { const t = i / 32; edge.push(pixelToLonLat(width * t, 0), pixelToLonLat(width, height * t), pixelToLonLat(width * t, height), pixelToLonLat(0, height * t)); }
  return validateBounds([Math.min(...edge.map(p => p[0])), Math.min(...edge.map(p => p[1])), Math.max(...edge.map(p => p[0])), Math.max(...edge.map(p => p[1]))]);
}
async function tiffPixels(blob) {
  const tiff = await fromArrayBuffer(await blob.arrayBuffer());
  const image = await tiff.getImage(0), rawDirectory = image.getFileDirectory(), keys = image.getGeoKeys() || {};
  const names = ['PhotometricInterpretation', 'BitsPerSample', 'SampleFormat', 'ExtraSamples', 'ModelTransformation', 'ModelPixelScale', 'ModelTiepoint'];
  // geotiff 3 exposes an ImageFileDirectory rather than the v2 property bag.
  const directory = Object.fromEntries(await Promise.all(names.map(async name => [name, rawDirectory.loadValue ? await rawDirectory.loadValue(name) : rawDirectory[name]])));
  const width = image.getWidth(), height = image.getHeight(); checkDimensions(width, height);
  const samples = image.getSamplesPerPixel(), bits = directory.BitsPerSample, formats = directory.SampleFormat;
  if (directory.PhotometricInterpretation !== 2 || samples < 3 || !bits || Array.from(bits).some(b => b !== 8) || (formats && Array.from(formats).some(v => v !== 1))) fail('Use an unsigned 8-bit RGB GeoTIFF. Convert multispectral, palette or high-bit-depth data in GIS software first; no automatic radiometric stretching is applied.');
  const code = Number(keys.ProjectedCSTypeGeoKey || keys.GeographicTypeGeoKey), crs = projectionForCode(code), transform = tiffTransform(directory, keys);
  const alpha = samples > 3 && directory.ExtraSamples && [1, 2].includes(Number(directory.ExtraSamples[0]));
  if (alpha && Number(directory.ExtraSamples[0]) === 1) fail('Premultiplied-alpha GeoTIFF is unsupported; export unassociated alpha or ordinary RGB.');
  const values = await image.readRasters({ samples: alpha ? [0, 1, 2, 3] : [0, 1, 2], interleave: true });
  const channels = alpha ? 4 : 3, data = new Uint8ClampedArray(width * height * 4), nodata = image.getGDALNoData();
  for (let i = 0, j = 0; i < width * height; i++, j += channels) {
    data[i * 4] = values[j]; data[i * 4 + 1] = values[j + 1]; data[i * 4 + 2] = values[j + 2];
    data[i * 4 + 3] = alpha ? values[j + 3] : 255;
    if (nodata !== null && values[j] === nodata && values[j + 1] === nodata && values[j + 2] === nodata) data[i * 4 + 3] = 0;
  }
  return { width, height, data, crs, transform };
}
function canvas(width, height) {
  const value = typeof OffscreenCanvas !== 'undefined' ? new OffscreenCanvas(width, height) : Object.assign(document.createElement('canvas'), { width, height });
  const context = value.getContext('2d', { willReadFrequently: true });
  if (!context) fail('This browser cannot decode image pixels. Use a current desktop browser.');
  return { value, context };
}
async function canvasBlob(value) {
  if (value.convertToBlob) return value.convertToBlob({ type: 'image/png' });
  return new Promise((resolve, reject) => value.toBlob(blob => blob ? resolve(blob) : reject(new Error('Cannot create the image preview.')), 'image/png'));
}
async function imagePixels(blob) {
  let bitmap;
  try { bitmap = await createImageBitmap(blob, { imageOrientation: 'none' }); } catch { fail('Cannot decode this image. Use an ordinary PNG, JPEG or georeferenced RGB GeoTIFF.'); }
  try {
    checkDimensions(bitmap.width, bitmap.height);
    const { context } = canvas(bitmap.width, bitmap.height); context.drawImage(bitmap, 0, 0);
    return { width: bitmap.width, height: bitmap.height, data: context.getImageData(0, 0, bitmap.width, bitmap.height).data };
  } finally { bitmap.close(); }
}
/** Rectified, display-only Web Mercator PNG; native raster bytes remain separate. */
async function preview(pixels, metadata) {
  const [w, s, e, n] = metadata.bounds, [xmin, ymin] = mercator([w, s]), [xmax, ymax] = mercator([e, n]);
  const ratio = (xmax - xmin) / (ymax - ymin), edge = Math.min(1024, Math.max(pixels.width, pixels.height));
  const width = ratio >= 1 ? edge : Math.max(2, Math.round(edge * ratio)), height = ratio >= 1 ? Math.max(2, Math.round(edge / ratio)) : edge;
  const { value, context } = canvas(width, height), output = context.createImageData(width, height), [a, b, c, d, ee, f] = metadata.transform, determinant = a * ee - b * d;
  const convert = proj4('EPSG:3857', metadata.crs);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const [gx, gy] = convert.forward([xmin + (x + 0.5) / width * (xmax - xmin), ymax - (y + 0.5) / height * (ymax - ymin)]);
    const sx = Math.floor((ee * (gx - c) - b * (gy - f)) / determinant), sy = Math.floor((-d * (gx - c) + a * (gy - f)) / determinant);
    if (sx < 0 || sy < 0 || sx >= pixels.width || sy >= pixels.height) continue;
    output.data.set(pixels.data.subarray((sy * pixels.width + sx) * 4, (sy * pixels.width + sx) * 4 + 4), (y * width + x) * 4);
  }
  context.putImageData(output, 0, 0);
  return { previewBlob: await canvasBlob(value), preview_width: width, preview_height: height, preview_crs: 'EPSG:3857', preview_bounds: metadata.bounds };
}
function details(pixels, { name, kind, attribution }) {
  const pixelToLonLat = pixelConverter(pixels), bounds = sourceBounds(pixels.width, pixels.height, pixelToLonLat);
  const center = mercator(pixelToLonLat(pixels.width / 2, pixels.height / 2)), nextX = mercator(pixelToLonLat(pixels.width / 2 + 1, pixels.height / 2)), nextY = mercator(pixelToLonLat(pixels.width / 2, pixels.height / 2 + 1));
  const latitude = pixelToLonLat(pixels.width / 2, pixels.height / 2)[1] * Math.PI / 180, factor = Math.cos(latitude);
  const resolution_xy_m = [Math.hypot(nextX[0] - center[0], nextX[1] - center[1]) * factor, Math.hypot(nextY[0] - center[0], nextY[1] - center[1]) * factor];
  return { name: name.slice(0, 180), kind, attribution, width: pixels.width, height: pixels.height, crs: pixels.crs, transform: pixels.transform, bounds, resolution_xy_m, resolution_m: (resolution_xy_m[0] + resolution_xy_m[1]) / 2, resolution_note: 'Pixel spacing estimate; not verified ground accuracy.', analysis_window_count: nativeWindowCount(pixels.width, pixels.height), maximum_windows_per_scan: 16, requires_scan_aoi: false, resampled: false, acquisition_date: null, georeferencing_note: kind === 'map' ? 'Provider Web Mercator export; source date and ground accuracy are not verified.' : 'Imported georeferencing or user-supplied bounds; ground accuracy is not verified.' };
}
function checkBlob(blob) { if (!(blob instanceof Blob) || blob.size === 0 || blob.size > SOURCE_LIMITS.bytes) fail('Choose a non-empty image up to 32 MB.'); }
function isTIFF(blob, signature) { return /tiff?/i.test(blob.type) || /\.tiff?$/i.test(blob.name || '') || (signature[0] === 73 && signature[1] === 73 && [42, 43].includes(signature[2])) || (signature[0] === 77 && signature[1] === 77 && [42, 43].includes(signature[3])); }
export async function importSource(file, bounds = null) {
  checkBlob(file);
  const signature = new Uint8Array(await file.slice(0, 4).arrayBuffer()), tiff = isTIFF(file, signature);
  const png = signature[0] === 137 && signature[1] === 80 && signature[2] === 78 && signature[3] === 71, jpeg = signature[0] === 255 && signature[1] === 216 && signature[2] === 255;
  if (!tiff && !png && !jpeg) fail('Unsupported image format. Choose a PNG, JPEG or georeferenced RGB GeoTIFF.');
  let pixels;
  if (tiff) pixels = await tiffPixels(file);
  else {
    const b = validateBounds(bounds); // Never use the current map view as implicit image georeferencing.
    pixels = await imagePixels(file); pixels.crs = 'EPSG:4326'; pixels.transform = [(b[2] - b[0]) / pixels.width, 0, b[0], 0, -(b[3] - b[1]) / pixels.height, b[3]];
  }
  const metadata = details(pixels, { name: file.name || (tiff ? 'Imported GeoTIFF' : 'Imported image'), kind: 'upload', attribution: 'User-supplied imagery. Importing an image does not establish its provenance or survey accuracy.' });
  metadata.raster_format = tiff ? 'geotiff' : 'image'; metadata.bounds_supplied_by_user = !tiff;
  const { previewBlob, ...previewMetadata } = await preview(pixels, metadata); Object.assign(metadata, previewMetadata);
  return { metadata, blob: file, previewBlob };
}
export async function getSourcePixels(source) {
  if (!source?.metadata) fail('Image metadata is missing. Re-import the image.');
  const metadata = source.metadata;
  let pixels;
  if (source.rawRGBA) pixels = { width: source.width, height: source.height, data: source.rawRGBA };
  else {
    checkBlob(source.blob);
    pixels = metadata.raster_format === 'geotiff' ? await tiffPixels(source.blob) : await imagePixels(source.blob);
  }
  checkDimensions(pixels.width, pixels.height);
  if (pixels.width !== metadata.width || pixels.height !== metadata.height || pixels.data.length !== pixels.width * pixels.height * 4) fail('Saved imagery dimensions do not match its metadata. Re-import the source.');
  const pixelToLonLat = pixelConverter(metadata);
  return { ...pixels, bounds: validateBounds(metadata.bounds), pixelToLonLat, lonLatToPixel: inversePixelConverter(metadata), metadata };
}
export function mapRequest(bounds) {
  const b = validateBounds(bounds), [xmin, ymin] = mercator(b.slice(0, 2)), [xmax, ymax] = mercator(b.slice(2, 4));
  const latitude = (b[1] + b[3]) / 2 * Math.PI / 180;
  if ((xmax - xmin) * Math.cos(latitude) > SOURCE_LIMITS.mapSideMetres || (ymax - ymin) * Math.cos(latitude) > SOURCE_LIMITS.mapSideMetres) fail('Choose a smaller map area: at most 500 metres per side.');
  const pixelSpacing = 2 * WEB_MERCATOR_EDGE / (256 * 2 ** 19), width = Math.ceil((xmax - xmin) / pixelSpacing), height = Math.ceil((ymax - ymin) / pixelSpacing);
  if (width > SOURCE_LIMITS.mapEdgePixels || height > SOURCE_LIMITS.mapEdgePixels) fail('This area exceeds the 2048-pixel map export limit at zoom 19. Select a smaller area; inference imagery will not be downsampled.');
  checkDimensions(width, height);
  const parameters = new URLSearchParams({ bbox: [xmin, ymin, xmax, ymax].join(','), bboxSR: '3857', imageSR: '3857', size: `${width},${height}`, format: 'png32', transparent: 'false', f: 'json' });
  return { url: `${MAP_SERVICE}?${parameters}`, width, height, transform: [(xmax - xmin) / width, 0, xmin, 0, -(ymax - ymin) / height, ymax], bounds: b };
}
export async function fetchMapSource(bounds, opts = {}) {
  const request = mapRequest(bounds), controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 30000);
  let response, blob, extent;
  const options = { mode: 'cors', credentials: 'omit', signal: opts.signal ? AbortSignal.any([opts.signal, controller.signal]) : controller.signal };
  try {
    response = await fetch(request.url, options);
    if (!response.ok) fail('The imagery provider could not export this map area.');
    const exportInfo = await response.json();
    extent = exportInfo.extent;
    const imageURL = new URL(exportInfo.href || '', request.url);
    if (exportInfo.error || !exportInfo.href || !['server.arcgisonline.com', 'services.arcgisonline.com'].includes(imageURL.hostname) || imageURL.protocol !== 'https:' || exportInfo.width !== request.width || exportInfo.height !== request.height || !extent || ![extent.xmin, extent.ymin, extent.xmax, extent.ymax].every(finite) || extent.xmin >= extent.xmax || extent.ymin >= extent.ymax || ![3857, 102100, 102113].includes(extent.spatialReference?.latestWkid || extent.spatialReference?.wkid)) fail('The imagery provider returned an invalid extent or export. No image was saved.');
    response = await fetch(imageURL.href, options);
    if (!response.ok || !/^image\//i.test(response.headers.get('content-type') || '')) fail('The imagery provider did not return an image. Choose another area or import georeferenced imagery.');
    const declaredLength = Number(response.headers.get('content-length'));
    if (Number.isFinite(declaredLength) && declaredLength > SOURCE_LIMITS.bytes) fail('The map image exceeds the 32 MB download limit.');
    blob = await response.blob(); checkBlob(blob);
  }
  catch (error) { if (error.status) throw error; fail(error.name === 'AbortError' ? 'Map request timed out or was cancelled. Try a smaller area.' : 'Cannot download the imagery with browser CORS access. Check your connection, or import georeferenced imagery.'); }
  finally { clearTimeout(timeout); }
  const pixels = await imagePixels(blob);
  if (pixels.width !== request.width || pixels.height !== request.height) fail('The imagery provider returned unexpected dimensions. No resampling or detection fallback was used.');
  Object.assign(pixels, { crs: 'EPSG:3857', transform: [(extent.xmax - extent.xmin) / pixels.width, 0, extent.xmin, 0, -(extent.ymax - extent.ymin) / pixels.height, extent.ymax] });
  const metadata = details(pixels, { name: opts.name || 'Map survey imagery', kind: 'map', attribution: ATTRIBUTION });
  Object.assign(metadata, { raster_format: 'image', preview_bounds: metadata.bounds, preview_crs: 'EPSG:3857', preview_width: pixels.width, preview_height: pixels.height, provider: 'Esri World Imagery', map_zoom: 19, downloaded_at: new Date().toISOString() });
  return { metadata, blob, previewBlob: blob, rawRGBA: pixels.data, width: pixels.width, height: pixels.height, bounds: metadata.bounds, pixelToLonLat: pixelConverter(metadata), lonLatToPixel: inversePixelConverter(metadata) };
}
