import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';
import assert from 'node:assert/strict';
import { init, parse } from 'es-module-lexer';

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
assert.ok(['atlas-private-preview-v1','atlas-private-release-v1'].includes(dataset.schema_version));
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
await init();
async function initialScriptBytes(content) {
  const seen = new Set();
  let size = 0;
  function visit(url) {
    if (seen.has(url.href)) return;
    seen.add(url.href);
    const script = readFileSync(url,'utf8');
    size += gzipSync(script).byteLength;
    for (const item of parse(script)[0]) {
      if (item.d === -1 && item.n && !item.n.startsWith('data:')) {
        visit(item.n.startsWith('/') ? new URL('.'+item.n,directory) : new URL(item.n,url));
      }
    }
  }
  for (const match of content.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
    const attrs = match[1];
    if (/type=["']application\/json["']/.test(attrs)) continue;
    const source = attrs.match(/\bsrc=["']([^"']+)["']/);
    if (source) visit(new URL('.'+source[1],directory));
    else size += gzipSync(match[2]).byteLength;
  }
  return size;
}
const initial = await initialScriptBytes(html);
assert.ok(initial < 300*1024,'Initial compressed explorer JavaScript exceeds 300 KiB');
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
const expected = ['sources/index.html','methods/index.html','about/index.html','downloads/index.html','records/index.html','minerals/index.html','countries/index.html','sites/index.html','search/index.html','pagefind/pagefind.js'];
if (dataset.mode === 'sites') {
  assert.ok(privatePreview, 'Release content is private-only');
  for (const record of dataset.records) {
    assert.ok(record.href === '/sites/'+record.id+'/', 'Durable site link required');
    expected.push('sites/'+record.id+'/index.html');
    for (const code of record.countries) expected.push('countries/'+code.toLowerCase()+'/index.html');
  }
}
const builtPaths = new Set(files);
for (const page of new Set(expected)) {
  assert.ok(builtPaths.has(fileURLToPath(new URL(page, directory))), `Missing ${page}`);
}
assert.ok(files.some(file => file.endsWith('.css') && /@media\s*print/.test(readFileSync(file, 'utf8'))));
console.log(`Verified ${privatePreview ? 'private' : 'standard'} build: ${dataset.records.length} entries; locally bundled assets; ${Math.ceil(initial / 1024)} KiB initial, ${Math.round(compressedJavaScript / 1024)} KiB total gzipped JavaScript (map is lazy-loaded).`);
