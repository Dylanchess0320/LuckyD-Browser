import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  // Relative asset paths so the built app works over file:// (Electron),
  // not just http:// (dev server). Without this the packaged UI is a blank
  // window because /assets/* resolves to the drive root.
  base: './',
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    // Dev-time proxy so the UI (5173) can call the Python backend (8000)
    // without CORS pain. In production (Electron) main.js injects the
    // token + base URL via window.__LUCKYD__ instead.
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
      '/health': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
