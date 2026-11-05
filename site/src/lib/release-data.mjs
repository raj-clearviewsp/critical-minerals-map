import { existsSync, readFileSync, statSync } from 'node:fs';
import { resolve } from 'node:path';
import { loadPreviewDataset, emptyPreview } from './preview-data.mjs';
import { MINERALS } from './preview-model.mjs';

export const RELEASE_SCHEMA = 'atlas-private-release-v1';
export { RUNG_LABELS, rungName } from './preview-model.mjs';
export const resolveReleasePath = () => resolve(process.env.ATLAS_RELEASE_PATH
  || '../data/releases/2026.11.04-v0/products/atlas.json');
const releaseCache = new Map();
let archiveCache;

function requireArray(value, name) {
  if (!Array.isArray(value)) throw new Error('Release requires an array of ' + name);
}
function uniqueKeys(rows, key, name) {
  const seen = new Set();
  for (const row of rows) {
    if (!row || typeof row[key] !== 'string' || !row[key].trim() || seen.has(row[key])) {
      throw new Error('Invalid or duplicate ' + name);
    }
    seen.add(row[key]);
  }
  return seen;
}
function textList(value, name) {
  if (!Array.isArray(value) || value.some(item => typeof item !== 'string' || !item.trim())) {
    throw new Error('Invalid ' + name);
  }
}
export function safeDownloadPath(path) {
  return typeof path === 'string' && /^downloads\/[A-Za-z0-9._/-]+$/.test(path)
    && /\.(?:csv|parquet|geoparquet|pmtiles|json|md|txt|zip)$/i.test(path)
    && path.split('/').every(segment => segment && segment !== '.' && segment !== '..');
}
/** @returns {import('./catalog-types').Release} */
export function validateRelease(value) {
  if (value?.schema_version !== RELEASE_SCHEMA || typeof value.release_tag !== 'string'
      || !value.release_tag.trim() || !(value.data_as_of === null || typeof value.data_as_of === 'string')) {
    throw new Error('Invalid Atlas private release metadata');
  }
  for (const key of ['sites','sources','countries','minerals','downloads']) requireArray(value[key], key);
  const sources = uniqueKeys(value.sources, 'id', 'source ID');
  const countries = uniqueKeys(value.countries, 'code', 'country code');
  uniqueKeys(value.minerals, 'code', 'mineral code');
  uniqueKeys(value.sites, 'id', 'site ID');
  uniqueKeys(value.downloads, 'path', 'download path');
  for (const profile of [...value.countries, ...value.minerals]) {
    if (typeof profile.name !== 'string' || !profile.name.trim()) throw new Error('Missing profile name');
    if (profile.context !== undefined) requireArray(profile.context, 'context observations');
  }
  if (value.countries.some(c => !/^[A-Z]{3}$/.test(c.code))
      || value.minerals.some(m => !Object.hasOwn(MINERALS, m.code))) throw new Error('Invalid profile code');
  for (const site of value.sites) {
    if (!/^[a-z0-9][a-z0-9_-]*$/i.test(site.id) || !site.name?.trim()) throw new Error('Unsafe site ID or empty name');
    textList(site.minerals, 'site minerals'); textList(site.country_iso3s, 'site countries');
    textList(site.source_ids, 'site sources');
    if (site.minerals.some(code => !Object.hasOwn(MINERALS, code))
      || site.country_iso3s.some(code => !countries.has(code)) || site.source_ids.some(id => !sources.has(id))) {
      throw new Error('Site refers to an unknown mineral, country, or source');
    }
    if (!(site.maturity_rung === null || Number.isInteger(site.maturity_rung) && site.maturity_rung >= 1 && site.maturity_rung <= 5)) throw new Error('Invalid site maturity rung');
    if (!(site.lat === null && site.lon === null) && !(Number.isFinite(site.lat) && Math.abs(site.lat) <= 90 && Number.isFinite(site.lon) && Math.abs(site.lon) <= 180)) throw new Error('Invalid site point');
    if (site.evidence !== undefined) requireArray(site.evidence, 'site evidence');
    if (site.evidence?.some(item => !sources.has(item.source_id))) throw new Error('Unknown site evidence source');
  }
  for (const file of value.downloads) {
    if (!safeDownloadPath(file.path) || !/^[a-f0-9]{64}$/.test(file.sha256)
      || typeof file.label !== 'string' || !file.label.trim()) throw new Error('Invalid approved download');
  }
  return value;
}
/** @returns {import('./catalog-types').Release | null} */
export function loadRelease(options = {}) {
  if (!(options.enabled ?? process.env.ATLAS_PRIVATE_PREVIEW === '1')) return null;
  const path = options.path ?? resolveReleasePath();
  if (!existsSync(path)) {
    if (options.path || process.env.ATLAS_RELEASE_PATH) throw new Error('Requested private release is missing: ' + path);
    return null;
  }
  const mtime = statSync(path).mtimeMs;
  const known = releaseCache.get(path);
  if (known?.mtime === mtime) return known.release;
  const release = validateRelease(JSON.parse(readFileSync(path, 'utf8')));
  releaseCache.set(path, { mtime, release });
  return release;
}
export function loadArchive() {
  if (process.env.ATLAS_PRIVATE_PREVIEW !== '1') return emptyPreview();
  const path = resolve(process.env.ATLAS_RECORDS_PATH || '../data/preview/site-v1/dataset.json');
  if (!existsSync(path)) return emptyPreview();
  if (archiveCache?.path === path) return archiveCache.value;
  const value = loadPreviewDataset({ enabled: true, path });
  archiveCache = { path, value };
  return value;
}
export function siteEntryCount(count) {
  return `${count.toLocaleString('en-US')} site ${count === 1 ? 'entry' : 'entries'}`;
}
export const siteUrl = id => '/sites/' + encodeURIComponent(id) + '/';
export const mineralUrl = code => '/minerals/' + encodeURIComponent(code.toLowerCase()) + '/';
export const countryUrl = code => '/countries/' + encodeURIComponent(code.toLowerCase()) + '/';
/** @param {import('./catalog-types').Release} release */
export function releaseExplorer(release) {
  const names = new Map(release.countries.map(c => [c.code, c.name]));
  const titles = new Map(release.sources.map(s => [s.id, s.title]));
  const records = release.sites.map(site => ({
    id: site.id, record_id: site.id, name: site.name, source_id: site.source_ids[0] || '',
    source_ids: site.source_ids, source_title: site.source_ids.map(id => titles.get(id)).join('; '),
    country: site.country_iso3s[0] || '', countries: site.country_iso3s,
    country_name: site.country_iso3s.map(code => names.get(code)).join('; '),
    minerals: site.minerals, entity_level: site.entity_level || 'unknown', lat: site.lat, lon: site.lon,
    location_precision: site.location_precision, kind: site.kind || 'deposit', status: site.status || '', status_as_of: site.status_as_of || '',
    maturity_rung: site.maturity_rung, maturity_reason: site.maturity_reason, reporting_system: site.reporting_system,
    operator: site.operator || '', operator_reference: (Array.isArray(site.operator_evidence) ? site.operator_evidence[0] : site.operator_evidence) || null,
    review_state: site.review_state || '', coincident_named_entries: site.coincident_named_entries || 1, production_families: site.production_families || [],
    aliases: site.aliases || [], context_names: [], href: siteUrl(site.id),
  }));
  return { schema_version: RELEASE_SCHEMA, mode: 'sites', release_tag: release.release_tag,
    data_as_of: release.data_as_of, tile_path: release.downloads.find(f=>f.path.endsWith('.pmtiles'))?.path || null, summary: { record_count: records.length }, records, sources: release.sources };
}
export function recordExplorer(archive, release) {
  if (!release) return { ...archive, mode: 'records' };
  const linked = new Map();
  for (const site of release.sites) {
    const members = site.record_ids?.length ? site.record_ids : (site.evidence || []).map(e=>e.record_id).filter(Boolean);
    const href = siteUrl(site.id);
    for (const id of members) {
      // A shared citation alone cannot assign a historical observation to one site.
      if (!linked.has(id)) linked.set(id, href);
      else if (linked.get(id) !== href) linked.set(id, null);
    }
  }
  return { ...archive, mode: 'records', records: archive.records.map(record =>
    ({ ...record, href: linked.get(record.record_id) || null })) };
}
