import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Vite exposes only prefixed variables to the bundle. Loftline writes
  // credentials under their descriptor names, so those names are listed
  // here as prefixes; a name is its own prefix.
  envPrefix: ['VITE_', 'STRIPE_PUBLISHABLE_KEY'],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
})
