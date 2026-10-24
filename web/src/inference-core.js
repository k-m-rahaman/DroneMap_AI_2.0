/** Pure YOLO segmentation helpers. Pixel coordinates refer to original source pixels. */
export const TILE_SIZE = 768;
export const TILE_OVERLAP = 128;
export const MAX_WINDOWS = 16;

export function windowPositions(length, tileSize = TILE_SIZE, overlap = TILE_OVERLAP) {
  if (!Number.isInteger(length) || length < 1 || tileSize <= overlap || overlap < 0) throw new Error('Invalid inference window dimensions.');
  if (length <= tileSize) return [0];
  const positions = [];
  for (let p = 0; p < length - tileSize; p += tileSize - overlap) positions.push(p);
  const last = length - tileSize;
  if (positions.at(-1) !== last) positions.push(last);
  return positions;
}

export function planWindows(width, height, region = [0, 0, width, height]) {
  if (![width, height, ...region].every(Number.isInteger) || width < 2 || height < 2 || region.length !== 4) throw new Error('Invalid source pixel dimensions.');
  const [left, top, right, bottom] = region;
  if (left < 0 || top < 0 || right > width || bottom > height || right <= left || bottom <= top) throw new Error('The scan area does not overlap the source imagery.');
  const xs = windowPositions(right - left), ys = windowPositions(bottom - top);
  if (xs.length * ys.length > MAX_WINDOWS) throw new Error(`This area requires ${xs.length * ys.length} native-resolution windows; the browser limit is ${MAX_WINDOWS}. Select a smaller area or split the image. Pixels will not be downsampled.`);
  return ys.flatMap(y => xs.map(x => ({x: left + x, y: top + y, width: Math.min(TILE_SIZE, right - left - x), height: Math.min(TILE_SIZE, bottom - top - y)})));
}

/** No resizing: each tile is at most 768 px and only receives 114-valued letterbox padding. */
export function letterboxRGBA(source, tile) {
  const {width, height, data} = source;
  if (!data || data.length !== width * height * 4 || tile.width > TILE_SIZE || tile.height > TILE_SIZE) throw new Error('Expected original RGBA source pixels.');
  const plane = TILE_SIZE * TILE_SIZE, tensor = new Float32Array(3 * plane);
  tensor.fill(114 / 255);
  const padX = Math.floor((TILE_SIZE - tile.width) / 2), padY = Math.floor((TILE_SIZE - tile.height) / 2);
  for (let y = 0; y < tile.height; y++) for (let x = 0; x < tile.width; x++) {
    const sourceIndex = ((tile.y + y) * width + tile.x + x) * 4, target = (y + padY) * TILE_SIZE + x + padX;
    if (data[sourceIndex + 3] === 0 || (source.validMask && !source.validMask[(tile.y + y) * width + tile.x + x])) continue;
    tensor[target] = data[sourceIndex] / 255;
    tensor[plane + target] = data[sourceIndex + 1] / 255;
    tensor[2 * plane + target] = data[sourceIndex + 2] / 255;
  }
  return {data: tensor, padX, padY, width: tile.width, height: tile.height};
}

export function boxIoU(a, b) {
  const intersection = Math.max(0, Math.min(a[2], b[2]) - Math.max(a[0], b[0])) * Math.max(0, Math.min(a[3], b[3]) - Math.max(a[1], b[1]));
  const areaA = Math.max(0, a[2] - a[0]) * Math.max(0, a[3] - a[1]);
  const areaB = Math.max(0, b[2] - b[0]) * Math.max(0, b[3] - b[1]);
  return intersection / Math.max(Number.EPSILON, areaA + areaB - intersection);
}

/** The exported one-class head is channel-first [1, 4 + 1 + 32, anchors]. */
export function decodeDetections(output, confidence = 0.5, iouThreshold = 0.7, maximum = 300) {
  const {data, dims} = output;
  if (dims?.length !== 3 || dims[0] !== 1 || dims[1] !== 37 || data?.length !== 37 * dims[2]) throw new Error('The building model has an unsupported prediction shape. Expected [1,37,N].');
  const count = dims[2], candidates = [];
  for (let index = 0; index < count; index++) {
    const score = data[4 * count + index];
    if (!Number.isFinite(score) || score <= confidence || score > 1) continue;
    const cx = data[index], cy = data[count + index], width = data[2 * count + index], height = data[3 * count + index];
    if (![cx, cy, width, height].every(Number.isFinite) || width <= 0 || height <= 0) continue;
    const coefficients = new Float32Array(32);
    for (let channel = 0; channel < 32; channel++) coefficients[channel] = data[(5 + channel) * count + index];
    if (!coefficients.every(Number.isFinite)) continue;
    candidates.push({index, confidence: score, box: [cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2], coefficients});
  }
  candidates.sort((a, b) => b.confidence - a.confidence || a.index - b.index);
  const kept = [];
  for (const candidate of candidates) {
    if (!kept.some(previous => boxIoU(candidate.box, previous.box) > iouThreshold)) kept.push(candidate);
    if (kept.length === maximum) break;
  }
  return kept;
}

/** Retina masks: coefficients @ prototypes, bilinear to 768, remove the actual padding, then threshold and crop.
 * The real padding is essential: a small native tile was deliberately NOT upscaled to 768.
 * Guessing a gain from its longest edge would move masks away from the original roof pixels.
 */
export function reconstructMask(prototypes, detection, letterbox, tile, source) {
  const {data, dims} = prototypes;
  if (dims?.length !== 4 || dims[0] !== 1 || dims[1] !== 32 || dims[2] !== 192 || dims[3] !== 192 || data?.length !== 32 * 192 * 192) throw new Error('The building model has unsupported mask prototypes. Expected [1,32,192,192].');
  const {width, height, padX, padY} = letterbox;
  const box = [Math.max(0, detection.box[0] - padX), Math.max(0, detection.box[1] - padY), Math.min(width, detection.box[2] - padX), Math.min(height, detection.box[3] - padY)];
  const x0 = Math.max(0, Math.ceil(box[0])), y0 = Math.max(0, Math.ceil(box[1]));
  const x1 = Math.min(width, Math.ceil(box[2])), y1 = Math.min(height, Math.ceil(box[3]));
  if (x1 <= x0 || y1 <= y0) return null;
  const size = 192 * 192, logits = new Float32Array(size);
  for (let c = 0; c < 32; c++) {
    const coefficient = detection.coefficients[c];
    if (coefficient === 0) continue;
    for (let p = 0; p < size; p++) logits[p] += coefficient * data[c * size + p];
  }
  const maskWidth = x1 - x0, maskHeight = y1 - y0, bits = new Uint8Array(maskWidth * maskHeight);
  let area = 0, invalidData = false;
  for (let y = y0; y < y1; y++) {
    const fy = Math.max(0, Math.min(191, (y + padY + 0.5) / 4 - 0.5));
    const ya = Math.floor(fy), yb = Math.min(191, ya + 1), wy = fy - ya;
    for (let x = x0; x < x1; x++) {
      const fx = Math.max(0, Math.min(191, (x + padX + 0.5) / 4 - 0.5));
      const xa = Math.floor(fx), xb = Math.min(191, xa + 1), wx = fx - xa;
      const upper = logits[ya * 192 + xa] * (1 - wx) + logits[ya * 192 + xb] * wx;
      const lower = logits[yb * 192 + xa] * (1 - wx) + logits[yb * 192 + xb] * wx;
      if (upper * (1 - wy) + lower * wy > 0) {
        bits[(y - y0) * maskWidth + x - x0] = 1; area++;
        const sourcePixel = (tile.y + y) * source.width + tile.x + x;
        if (source.data[sourcePixel * 4 + 3] === 0 || (source.validMask && !source.validMask[sourcePixel])) invalidData = true;
      }
    }
  }
  return {x: tile.x + x0, y: tile.y + y0, width: maskWidth, height: maskHeight, data: bits, area, invalidData};
}

export function maskOverlap(a, b) {
  const left = Math.max(a.x, b.x), top = Math.max(a.y, b.y), right = Math.min(a.x + a.width, b.x + b.width), bottom = Math.min(a.y + a.height, b.y + b.height);
  let intersection = 0;
  for (let y = top; y < bottom; y++) for (let x = left; x < right; x++) {
    if (a.data[(y - a.y) * a.width + x - a.x] && b.data[(y - b.y) * b.width + x - b.x]) intersection++;
  }
  return {intersection, intersectionOverMinimum: intersection / Math.max(1, Math.min(a.area, b.area)), iou: intersection / Math.max(1, a.area + b.area - intersection)};
}

export function deduplicateMasks(candidates) {
  const sorted = [...candidates].sort((a, b) => Number(a.truncated) - Number(b.truncated) || b.confidence - a.confidence || b.mask.area - a.mask.area);
  const kept = [];
  for (const candidate of sorted) if (!kept.some(previous => {
    const overlap = maskOverlap(candidate.mask, previous.mask);
    return overlap.intersection > 0 && (overlap.intersectionOverMinimum >= 0.8 || overlap.iou >= 0.45);
  })) kept.push(candidate);
  return kept;
}

export function ringArea(ring) {
  let total = 0;
  for (let i = 0; i < ring.length - 1; i++) total += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
  return total / 2;
}

function pointInRing(point, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[i], b = ring[j];
    if ((a[1] > point[1]) !== (b[1] > point[1]) && point[0] < (b[0] - a[0]) * (point[1] - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside;
  }
  return inside;
}

function simplifyCollinear(ring) {
  const open = ring.slice(0, -1), kept = [];
  for (let i = 0; i < open.length; i++) {
    const a = open[(i + open.length - 1) % open.length], b = open[i], c = open[(i + 1) % open.length];
    if ((b[0] - a[0]) * (c[1] - b[1]) !== (b[1] - a[1]) * (c[0] - b[0])) kept.push(b);
  }
  return kept.length >= 3 ? [...kept, kept[0]] : [];
}

/** Trace actual pixel-cell boundaries, retaining concavities, holes and separate components. No box substitute. */
export function maskPolygons(mask) {
  const {width, height, data} = mask, edges = [], outgoing = new Map();
  const present = (x, y) => x >= 0 && y >= 0 && x < width && y < height && data[y * width + x] !== 0;
  const add = (x, y, xx, yy, direction) => {
    const edge = {x, y, xx, yy, direction, used: false};
    const key = y * (width + 1) + x;
    if (!outgoing.has(key)) outgoing.set(key, []);
    outgoing.get(key).push(edge); edges.push(edge);
  };
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) if (present(x, y)) {
    if (!present(x, y - 1)) add(x, y, x + 1, y, 0);
    if (!present(x + 1, y)) add(x + 1, y, x + 1, y + 1, 1);
    if (!present(x, y + 1)) add(x + 1, y + 1, x, y + 1, 2);
    if (!present(x - 1, y)) add(x, y + 1, x, y, 3);
  }
  const rings = [];
  for (const first of edges) {
    if (first.used) continue;
    const ring = [[first.x + mask.x, first.y + mask.y]];
    let edge = first, guard = 0;
    do {
      edge.used = true; ring.push([edge.xx + mask.x, edge.yy + mask.y]);
      if (edge.xx === first.x && edge.yy === first.y) break;
      const next = (outgoing.get(edge.yy * (width + 1) + edge.xx) || []).filter(candidate => !candidate.used);
      // At a diagonal saddle, keep four-connected pixel components separate.
      const turnOrder = [1, 0, 3, 2];
      next.sort((a, b) => turnOrder.indexOf((a.direction - edge.direction + 4) % 4) - turnOrder.indexOf((b.direction - edge.direction + 4) % 4));
      edge = next[0];
      if (!edge || ++guard > edges.length) throw new Error('Could not close a segmentation contour.');
    } while (true);
    const simplified = simplifyCollinear(ring);
    if (simplified.length >= 4) rings.push(simplified);
  }
  const polygons = rings.filter(ring => ringArea(ring) > 0).map(ring => [ring]);
  for (const hole of rings.filter(ring => ringArea(ring) < 0)) {
    const parent = polygons.filter(polygon => pointInRing(hole[0], polygon[0])).sort((a, b) => Math.abs(ringArea(a[0])) - Math.abs(ringArea(b[0])))[0];
    if (parent) parent.push(hole);
  }
  return polygons;
}
