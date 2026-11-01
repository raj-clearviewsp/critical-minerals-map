import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * @typedef {Object} PreviewRecord
 * @property {string} id
 * @property {string} record_id
 * @property {string} source_id
 * @property {string} name
 * @property {string} country
 * @property {string} country_name
 * @property {string[]} minerals
 * @property {string} entity_level
 * @property {number} lat
 * @property {number} lon
 * @property {string} location_precision
 * @property {string} status
 * @property {string[]} deposit_types
 * @property {string[]} aliases
 * @property {string[]} context_names
 * @property {string} source_url
 * @property {string} source_title
 * @property {string|null} source_date
 * @property {string} source_license
 * @property {string} provenance
 * @property {string} identity_review
 * @typedef {{id:string,title:string,url:string,license:string,redistribution_ok:boolean,retrieved_at:string}} PreviewSource
 * @typedef {{schema_version:string,records:PreviewRecord[],sources:PreviewSource[],summary:{record_count:number,source_count:number,known_entity_level_counts:Object},data_as_of:null|string,release_tag:string,private_preview:boolean}} PreviewDataset
 */

/** @returns {PreviewDataset} */
export const emptyPreview = () => ({
  schema_version: 'atlas-private-preview-v1', records: [], sources: [],
  summary: { record_count: 0, source_count: 0, known_entity_level_counts: {} },
  data_as_of: null, release_tag: '2026.11.01-preview', private_preview: false,
});

/** @returns {PreviewDataset} */
export function validatePreviewDataset(value) {
  if (!value || value.schema_version !== 'atlas-private-preview-v1'
      || !Array.isArray(value.records) || !Array.isArray(value.sources)
      || value.summary?.record_count !== value.records.length
      || value.summary?.source_count !== value.sources.length) {
    throw new Error('Invalid private preview dataset contract.');
  }
  const ids = new Set();
  const sources = new Set(value.sources.map(source => source.id));
  for (const record of value.records) {
    if (!record || typeof record.id !== 'string' || !/^[a-z0-9_-]+$/i.test(record.id)
        || ids.has(record.id) || typeof record.name !== 'string'
        || typeof record.record_id !== 'string' || !sources.has(record.source_id)
        || !Array.isArray(record.minerals) || !record.minerals.length
        || record.minerals.some(mineral => !['REE', 'Ni', 'Co'].includes(mineral))
        || !Number.isFinite(record.lon) || Math.abs(record.lon) > 180
        || !Number.isFinite(record.lat) || Math.abs(record.lat) > 90) {
      throw new Error('Invalid or duplicate private preview source record.');
    }
    ids.add(record.id);
  }
  return value;
}

/** @returns {PreviewDataset} */
export function loadPreviewDataset(options = {}) {
  const enabled = options.enabled ?? process.env.ATLAS_PRIVATE_PREVIEW === '1';
  if (!enabled) return emptyPreview();
  const path = options.path ?? resolve(process.cwd(), '../data/preview/site-v1/dataset.json');
  return { ...validatePreviewDataset(JSON.parse(readFileSync(path, 'utf8'))), private_preview: true };
}

export function escapeJson(value) {
  return JSON.stringify(value).replaceAll('<', '\\u003c').replaceAll('>', '\\u003e')
    .replaceAll('&', '\\u0026').replaceAll('\u2028', '\\u2028').replaceAll('\u2029', '\\u2029');
}
