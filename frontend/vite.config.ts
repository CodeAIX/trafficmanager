import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist' },
  server: { proxy: { '/api': 'http://localhost:2096', '/auth': 'http://localhost:2096', '/health': 'http://localhost:2096' } }
})
