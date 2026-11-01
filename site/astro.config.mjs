import { defineConfig } from 'astro/config';

export default defineConfig({
  output: 'static',
  outDir: process.env.ATLAS_PRIVATE_PREVIEW === '1' ? './.preview-dist' : './dist',
  devToolbar: { enabled: false },
  build: { inlineStylesheets: 'never' },
});
