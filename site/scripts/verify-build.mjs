import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const directory = new URL('../dist/', import.meta.url);
const html = readFileSync(new URL('index.html', directory), 'utf8');
assert.match(html, /No records loaded/);
assert.match(html, /Data as of/);
assert.match(html, /<html[^>]+lang="en"/);
assert.match(html, /<main[^>]+id="main"/);
assert.doesNotMatch(html, /<script\b/);
assert.doesNotMatch(html, /(?:src|href)=["']https?:\/\//);
assert.doesNotMatch(html, /<iframe\b/);
function inspect(path) {
  for (const item of readdirSync(path, { withFileTypes: true })) {
    const entry = join(path, item.name);
    if (item.isDirectory()) inspect(entry);
    else if (item.name.endsWith('.css')) {
      const css = readFileSync(entry, 'utf8');
      assert.doesNotMatch(css, /@import|url\(["']?https?:/);
      assert.match(css, /@media print/);
    } else assert.ok(!item.name.endsWith('.js'), `Unexpected client script: ${entry}`);
  }
}
inspect(fileURLToPath(directory));
console.log('Static build verified: empty-data status, print styles, no client JavaScript or remote assets.');
