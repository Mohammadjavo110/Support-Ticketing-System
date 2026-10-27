import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Vite dev server for the support-ticket frontend.
// - host/port are fixed because docker-compose maps 5173:5173.
// - /api (and /healthz) are proxied to the api service so the browser can
//   call them same-origin when VITE_API_BASE="" is set. The default base in
//   src/api.js is the absolute http://localhost:8000.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/healthz': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
