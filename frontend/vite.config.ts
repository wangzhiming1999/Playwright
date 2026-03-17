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
      '/run': 'http://127.0.0.1:8000',
      '/tasks': 'http://127.0.0.1:8000',
      '/screenshots': 'http://127.0.0.1:8000',
      '/curate': 'http://127.0.0.1:8000',
      '/generate': 'http://127.0.0.1:8000',
      '/explore': 'http://127.0.0.1:8000',
      '/export': 'http://127.0.0.1:8000',
      '/workflows': 'http://127.0.0.1:8000',
      '/workflow-runs': 'http://127.0.0.1:8000',
      '/cleanup': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
    },
  },
})
