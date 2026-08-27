import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

/** The storefront is a separate site on a separate port.
 *
 * That separation is not cosmetic. Cart abandonment only reads as real if the shop
 * is something the customer can actually close — and the recovery message has to
 * arrive somewhere else, after they have gone. Running it inside the dashboard as a
 * tab makes the whole beat look staged.
 *
 * It shares this project's node_modules, tailwind tokens and components; only the
 * entry point and the port differ.
 */
export default defineConfig({
  plugins: [
    react(),
    {
      // Serve shop.html at "/" so the URL is localhost:5174, not /shop.html.
      name: 'shop-root',
      configureServer: (server) => {
        server.middlewares.use((req, _res, next) => {
          if (req.url === '/' || req.url === '/index.html') req.url = '/shop.html'
          next()
        })
      },
    },
  ],
  server: {
    port: 5174,
    proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } },
  },
  build: { outDir: 'dist-shop', rollupOptions: { input: 'shop.html' } },
})
