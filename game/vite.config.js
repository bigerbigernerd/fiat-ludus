import { defineConfig } from 'vite';

// base './' keeps every URL relative so dist/ can be hosted under any sub-path.
export default defineConfig({
  base: './',
  // assetsDir 'build' keeps Vite's hashed files apart from public/assets (the manifest folder).
  build: { outDir: 'dist', assetsDir: 'build', assetsInlineLimit: 0, chunkSizeWarningLimit: 900 },
  server: { host: true },
});
