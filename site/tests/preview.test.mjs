import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { loadPreviewDataset, validatePreviewDataset, escapeJson } from '../src/lib/preview-data.mjs';
import { filterRecords, withinBounds, parseView, serializeView, safeSourceUrl, recordsCsv } from '../src/lib/preview-model.mjs';

const first = { id: 'sample-1', record_id: 'r1', source_id: 'source-a', name: 'Café Ridge',
  country: 'CAN', country_name: 'Canada', minerals: ['Ni', 'Co'], entity_level: 'site', lat: 45, lon: -75,
  source_title: 'Test archive', aliases: ['Old ridge'], context_names: [], source_date: null };
const context = { ...first, id: 'sample-2', record_id: 'r2', name: 'Wide district', entity_level: 'district_area', minerals: ['REE'] };
const unknown = { ...first, id: 'sample-3', record_id: 'r3', name: 'Unnamed scope', entity_level: 'unknown', country: '', country_name: '' };
const dataset = () => ({ schema_version: 'atlas-private-preview-v1', records: [first], sources: [{ id: 'source-a' }], summary: { record_count: 1, source_count: 1 } });

test('default loader does not access a supplied private path', () => {
  const result = loadPreviewDataset({ enabled: false, path: '/a/private/path/that/does/not/exist.json' });
  assert.deepEqual(result.records, []);
  assert.deepEqual(result.sources, []);
  assert.equal(result.private_preview, false);
});
test('private loading is explicit and validates the contract', () => {
  const directory = mkdtempSync(join(tmpdir(), 'atlas-preview-test-'));
  try {
    const path = join(directory, 'dataset.json');
    writeFileSync(path, JSON.stringify(dataset()));
    assert.equal(loadPreviewDataset({ enabled: true, path }).records[0].name, first.name);
    writeFileSync(path, '{"schema_version":"wrong"}');
    assert.throws(() => loadPreviewDataset({ enabled: true, path }), /contract/);
  } finally { rmSync(directory, { recursive: true }); }
});
test('duplicate identities, unknown sources and invalid coordinates are rejected', () => {
  const duplicate = dataset(); duplicate.records.push(first); duplicate.summary.record_count = 2;
  assert.throws(() => validatePreviewDataset(duplicate), /duplicate/);
  for (const change of [{ source_id: 'missing' }, { lat: 91 }, { lon: Number.NaN }, { minerals: ['Au'] }]) {
    const value = dataset(); value.records = [{ ...first, ...change }];
    assert.throws(() => validatePreviewDataset(value), /Invalid/);
  }
});
test('broader records are opt-in while unknown scope remains visible', () => {
  assert.deepEqual(filterRecords([first, context, unknown], {}).map(record => record.id), ['sample-1', 'sample-3']);
  assert.equal(filterRecords([first, context, unknown], { context: true }).length, 3);
});
test('mineral, country, source and accent-insensitive names combine', () => {
  assert.equal(filterRecords([first, context], { q: 'cafe old', mineral: 'Co', country: 'CAN', source: 'source-a' }).length, 1);
  assert.equal(filterRecords([first], { mineral: 'REE' }).length, 0);
  assert.equal(filterRecords([unknown], { country: 'unknown' }).length, 1);
});
test('viewport filtering handles a crossing of the antimeridian', () => {
  const bounds = [170, -20, 190, 20];
  assert.equal(withinBounds({ lon: -179, lat: 0 }, bounds), true);
  assert.equal(withinBounds({ lon: 179, lat: 0 }, bounds), true);
  assert.equal(withinBounds({ lon: 0, lat: 0 }, bounds), false);
  assert.equal(withinBounds({ lon: -179, lat: 30 }, bounds), false);
  assert.equal(withinBounds({ lon: 0, lat: 0 }, [-200, -90, 200, 90]), true);
});
test('URL state preserves filters and selection with a bounded view', () => {
  const state = { q: 'Café Ridge & coast', mineral: 'Ni', country: 'CAN', source: 'source-a', context: true, visible: true, selected: 'sample-1' };
  const restored = parseView(serializeView(state, { center: [-75.123456, 45.123456], zoom: 8.345 }));
  for (const [key, value] of Object.entries(state)) assert.equal(restored[key], value);
  assert.deepEqual(restored.center, [-75.1235, 45.1235]);
  assert.equal(restored.zoom, 8.35);
  assert.equal(parseView('?center=1,99&zoom=999&mineral=Au').center, null);
  assert.equal(parseView('?zoom=999').zoom, null);
  assert.equal(parseView('?mineral=Au').mineral, '');
});
test('citations accept HTTPS and reject executable or credential-bearing links', () => {
  assert.equal(safeSourceUrl('https://example.org/source'), 'https://example.org/source');
  for (const value of ['javascript:alert(1)', 'data:text/html,test', 'http://example.org', 'https://user:secret@example.org', '/relative']) {
    assert.equal(safeSourceUrl(value), null);
  }
});
test('embedded data cannot close its JSON script', () => {
  const value = { name: '</script><script>alert("x")</script> & \u2028' };
  const escaped = escapeJson(value);
  assert.equal(escaped.includes('<'), false);
  assert.deepEqual(JSON.parse(escaped), value);
});
test('CSV quotes source text and preserves negative numeric coordinates', () => {
  const csv = recordsCsv([{ ...first, name: '=HYPERLINK("bad")', status: 'Quoted "status", second line\ncontinued' }]);
  assert.match(csv, /"'=HYPERLINK\(""bad""\)"/);
  assert.match(csv, /"45","-75"/);
  assert.match(csv, /"Quoted ""status"", second line\ncontinued"/);
  assert.match(csv, /"Ni; Co"/);
});
