import { defineConfig } from 'vite';

// The game server (FastAPI) listens on port 8000. See docs/CONTRACTS.md.
const target = 'http://localhost:8000';

export default defineConfig({
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target, changeOrigin: true },
      '/ws': { target, ws: true, changeOrigin: true },
    },
  },
  preview: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target, changeOrigin: true },
      '/ws': { target, ws: true, changeOrigin: true },
    },
  },
  build: {
    chunkSizeWarningLimit: 1200,
  },
});
