import { join } from 'node:path';
import * as pagefind from 'pagefind';

export async function buildSearch(directory) {
  try {
    const result = await pagefind.createIndex({ rootSelector: 'main', forceLanguage: 'en', maxFragments: 250 });
    if (!result.index || result.errors.length) throw new Error('Pagefind index could not be created: '+result.errors.join('; '));
    const added = await result.index.addDirectory({ path: directory, glob: '**/*.html' });
    if (added.errors.length || !added.page_count) throw new Error('Search indexing failed: '+added.errors.join('; '));
    const written = await result.index.writeFiles({ outputPath: join(directory,'pagefind') });
    if (written.errors.length) throw new Error('Search index write failed: '+written.errors.join('; '));
    await result.index.deleteIndex();
    console.log('Indexed '+added.page_count+' Atlas pages for local search.');
  } finally { await pagefind.close(); }
}
