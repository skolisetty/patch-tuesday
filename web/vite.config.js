import { defineConfig } from 'vite';

// The game server (FastAPI) listens on port 8000. See docs/CONTRACTS.md.
// PT_SERVER_PORT and PT_WEB_PORT let a second instance run next to the live one.
function port(name, fallback) {
  const n = Number(process.env[name]);
  return Number.isInteger(n) && n > 0 && n < 65536 ? n : fallback;
}

const target = `http://localhost:${port('PT_SERVER_PORT', 8000)}`;
const webPort = port('PT_WEB_PORT', 5173);

export default defineConfig({
  server: {
    port: webPort,
    strictPort: true,
    proxy: {
      '/api': { target, changeOrigin: true },
      '/ws': { target, ws: true, changeOrigin: true },
    },
  },
  preview: {
    port: webPort,
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
