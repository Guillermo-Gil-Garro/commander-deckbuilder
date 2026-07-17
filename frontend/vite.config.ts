import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

// The API routes live at the root (no /api prefix) because in production FastAPI
// serves both the API and this SPA from the same origin. In dev the SPA is on
// :5173 and the API on :8000, which would be cross-origin — and the API sends no
// CORS headers, since it never needs them in production. Proxying each API route
// keeps dev same-origin too, so the client uses relative paths everywhere and dev
// matches prod instead of depending on CORS.
const API_ROUTES = [
  '/health',
  '/commanders',
  '/banlist',
  '/cards',
  '/why-not',
  '/structure',
  '/build',
  '/sequential',
  '/maybeboard',
  '/audit',
  '/export',
];

const API_TARGET = 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: Object.fromEntries(
      API_ROUTES.map((route) => [route, { target: API_TARGET, changeOrigin: true }]),
    ),
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
  },
});
