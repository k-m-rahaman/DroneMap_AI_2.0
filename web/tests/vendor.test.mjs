import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import vm from 'node:vm';

const root = new URL('../vendor/', import.meta.url);

test('every vendored upstream file matches its recorded byte count and SHA-256', async () => {
  const manifest = JSON.parse(await readFile(new URL('manifest.json', root), 'utf8'));
  assert(manifest.assets.length >= 7);
  for (const asset of manifest.assets) {
    assert(!asset.file.includes('/') && !asset.file.includes('..'));
    const bytes = await readFile(new URL(asset.file, root));
    assert.equal(bytes.length, asset.bytes, asset.file);
    assert.equal(createHash('sha256').update(bytes).digest('hex'), asset.sha256, asset.file);
    assert.match(asset.source, /^https:\/\/(cdn\.tailwindcss\.com|raw\.githubusercontent\.com|registry\.npmjs\.org)\//);
    assert((await readFile(new URL(asset.license, root), 'utf8')).includes('Copyright'));
  }
});

test('the vendored Turf UMD exposes the actual two-argument polygon intersection API used by the UI', async () => {
  const context = vm.createContext({});
  vm.runInContext(await readFile(new URL('turf-6.5.0.min.js', root), 'utf8'), context, {timeout: 2000});
  const turf = context.turf;
  for (const api of ['area', 'intersect', 'booleanIntersects', 'polygon', 'featureCollection']) assert.equal(typeof turf[api], 'function', api);
  const a = turf.polygon([[[0, 0], [0.002, 0], [0.002, 0.002], [0, 0.002], [0, 0]]]);
  const b = turf.polygon([[[0.001, 0.001], [0.003, 0.001], [0.003, 0.003], [0.001, 0.003], [0.001, 0.001]]]);
  const overlap = turf.intersect(a, b);
  assert.equal(overlap.geometry.type, 'Polygon');
  assert(turf.area(overlap) > 12000 && turf.area(overlap) < 12500);
  assert.equal(turf.booleanIntersects(a, b), true);
});

test('geocoder stylesheet has only embedded data URLs and does not import external CSS', async () => {
  const css = await readFile(new URL('Control.Geocoder-4.0.0.css', root), 'utf8');
  assert(!/@import/i.test(css));
  const resources = [...css.matchAll(/url\(["']?([^"')]+)/g)].map(match => match[1]);
  assert(resources.length >= 2);
  for (const resource of resources) assert(resource.startsWith('data:image/svg+xml,'));
});
