import test from 'node:test';
import assert from 'node:assert/strict';
import { validateManifest } from '../src/lib/manifest.mjs';

const empty = () => ({
  release_tag: '2026.10.28',
  data_as_of: null,
  status: 'scaffold',
  tables: { site: { rows: 0, csv: 'site.csv', parquet: 'site.parquet' } },
  sources: [],
});

test('accepts an empty pipeline manifest', () => {
  const manifest = empty();
  assert.equal(validateManifest(manifest), manifest);
});

test('refuses malformed or missing metadata', () => {
  for (const value of [null, [], {}, { ...empty(), release_tag: 'latest' }, { ...empty(), tables: [] }]) {
    assert.throws(() => validateManifest(value));
  }
});

test('refuses real records or source claims before ingestion is implemented', () => {
  assert.throws(() => validateManifest({ ...empty(), tables: { site: { rows: 1 } } }));
  assert.throws(() => validateManifest({ ...empty(), sources: ['unreviewed'] }));
  assert.throws(() => validateManifest({ ...empty(), data_as_of: '2026-10-28' }));
  assert.throws(() => validateManifest({ ...empty(), status: 'published' }));
});
