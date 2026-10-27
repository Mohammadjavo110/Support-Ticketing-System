import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Vite dev server for the support-ticket frontend.
// - host/port are fixed because docker-compose maps 5173:5173.
// - /api (and /healthz) are proxied to the api service so the browser can call
//   them same-origin when VITE_API_BASE="" (what docker-compose sets).
//   VITE_PROXY_TARGET selects the upstream: compose points it at
//   http://api:8000 (service name on the compose network), while running the
//   dev server on the host keeps the default http://localhost:8000.
const proxyTarget = process.env.VITE_PROXY_TARGET || 'http://localhost:8000';
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: proxyTarget,
        changeOrigin: true,
      },
      '/healthz': {
        target: proxyTarget,
        changeOrigin: true,
      },
    },
  },
});
