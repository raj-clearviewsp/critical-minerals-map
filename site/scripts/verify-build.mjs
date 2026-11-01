import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';
import assert from 'node:assert/strict';

const privatePreview = process.env.ATLAS_PRIVATE_PREVIEW === '1';
const directory = new URL(privatePreview ? '../.preview-dist/' : '../dist/', import.meta.url);
const html = readFileSync(new URL('index.html', directory), 'utf8');
assert.match(html, /<html[^>]+lang="en"/);
assert.match(html, /<main[^>]+id="main"/);
assert.match(html, /noindex/);
assert.doesNotMatch(html, /<iframe\b/);
const embedded = html.match(/<script\b(?=[^>]*id="atlas-preview-data")[^>]*>([\s\S]*?)<\/script>/);
assert.ok(embedded, 'Explorer must bind its data to the built page.');
const dataset = JSON.parse(embedded[1]);
assert.equal(dataset.schema_version, 'atlas-private-preview-v1');
assert.equal(dataset.records.length, dataset.summary.record_count);
if (privatePreview) assert.ok(dataset.records.length > 0, 'Private build needs verified source records.');
else {
  assert.equal(dataset.records.length, 0, 'Standard build must never load private observations.');
  assert.equal(dataset.sources.length, 0, 'Standard build must never load the private source catalog.');
}
const files = [];
function inspect(path) {
  for (const item of readdirSync(path, { withFileTypes: true })) {
    const entry = join(path, item.name);
    if (item.isDirectory()) inspect(entry);
    else files.push(entry);
  }
}
inspect(fileURLToPath(directory));
let compressedJavaScript = 0;
for (const file of files) {
  if (file.endsWith('.html')) {
    const page = readFileSync(file, 'utf8');
    assert.doesNotMatch(page, /<(?:script|img|iframe)\b[^>]*src=["']https?:\/\//);
    assert.doesNotMatch(page, /<link\b[^>]*href=["']https?:\/\//);
    if (!privatePreview) assert.doesNotMatch(page, /Raj Sawhney|ICMM06772|COM00910|PRIVATE REVIEWER|PRIVATE NOTE/);
  }
  if (file.endsWith('.css')) assert.doesNotMatch(readFileSync(file, 'utf8'), /@import|url\(["']?https?:/);
  if (/\.m?js$/.test(file)) compressedJavaScript += gzipSync(readFileSync(file)).byteLength;
}
assert.ok(files.some(file => file.endsWith('countries.geojson')), 'Local basemap is required.');
assert.ok(files.some(file => /maplibre-gl-worker[^/]*\.mjs$/.test(file)), 'Local MapLibre worker is required.');
for (const page of ['sources/index.html', 'about/index.html']) {
  assert.ok(files.includes(fileURLToPath(new URL(page, directory))), `Missing ${page}`);
}
assert.ok(files.some(file => file.endsWith('.css') && /@media\s*print/.test(readFileSync(file, 'utf8'))));
console.log(`Verified ${privatePreview ? 'private' : 'standard'} build: ${dataset.records.length} source records; locally bundled assets; ${Math.round(compressedJavaScript / 1024)} KiB total gzipped JavaScript (map is lazy-loaded).`);
