import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The backend runs on :8000. Proxying keeps every fetch a same-origin
    // relative path, so VITE_USE_MOCK can swap the data source without any
    // component knowing where the data came from.
    proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } },
  },
})
