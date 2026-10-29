import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/** Validate the scaffold contract without interpreting empty tables as mineral observations. */
export function validateManifest(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    throw new Error('Expected an Atlas publish manifest object. Run make publish.');
  }
  if (typeof input.release_tag !== 'string' || !/^\d{4}\.\d{2}\.\d{2}$/.test(input.release_tag)) {
    throw new Error('Manifest release_tag must use YYYY.MM.DD.');
  }
  if (input.status !== 'scaffold' || input.data_as_of !== null) {
    throw new Error('Step 1 site accepts only an empty scaffold manifest.');
  }
  if (!Array.isArray(input.sources) || input.sources.length !== 0) {
    throw new Error('Step 1 must not expose unreviewed sources.');
  }
  if (!input.tables || typeof input.tables !== 'object' || Array.isArray(input.tables)) {
    throw new Error('Manifest tables must be an object.');
  }
  for (const [name, table] of Object.entries(input.tables)) {
    if (!table || typeof table !== 'object' || table.rows !== 0) {
      throw new Error(`Step 1 table ${name} must contain zero rows.`);
    }
    if (typeof table.csv !== 'string' || typeof table.parquet !== 'string') {
      throw new Error(`Step 1 table ${name} must declare CSV and Parquet paths.`);
    }
  }
  return input;
}

// Astro relocates this module when bundling. npm runs scripts from site/, so
// resolve the pipeline contract from the working directory, not import.meta.url.
export function loadManifest(manifestPath = resolve(process.cwd(), '../data/publish/manifest.json')) {
  try {
    return validateManifest(JSON.parse(readFileSync(manifestPath, 'utf8')));
  } catch (error) {
    throw new Error(`Cannot build Atlas site: ${error.message}`, { cause: error });
  }
}
