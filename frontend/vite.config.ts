import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  build: {
    outDir: '../static',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/run': 'http://localhost:8000',
      '/tasks': 'http://localhost:8000',
      '/screenshots': 'http://localhost:8000',
      '/curate': 'http://localhost:8000',
      '/generate': 'http://localhost:8000',
      '/explore': 'http://localhost:8000',
      '/export': 'http://localhost:8000',
      '/workflows': 'http://localhost:8000',
      '/workflow-runs': 'http://localhost:8000',
      '/cleanup': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})
