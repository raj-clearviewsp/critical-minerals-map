import { defineConfig } from 'astro/config';
import { fileURLToPath } from 'node:url';
import { loadRelease, resolveReleasePath } from './src/lib/release-data.mjs';
import { buildSearch } from './scripts/build-search.mjs';
import { copyDownloads } from './scripts/copy-downloads.mjs';

export default defineConfig({
  output: 'static',
  outDir: process.env.ATLAS_PRIVATE_PREVIEW === '1' ? './.preview-dist' : './dist',
  devToolbar: { enabled: false },
  build: { inlineStylesheets: 'never' },
  integrations: [{
    name: 'atlas-declared-downloads',
    hooks: { 'astro:build:done': async ({ dir }) => {
      const release = loadRelease();
      if (release) await copyDownloads(release, resolveReleasePath(), fileURLToPath(dir));
      await buildSearch(fileURLToPath(dir));
    } },
  }],
});
