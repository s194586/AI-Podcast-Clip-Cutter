import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/health': 'http://127.0.0.1:8010',
      '/project': 'http://127.0.0.1:8010',
      '/projects': 'http://127.0.0.1:8010',
      '/clips': 'http://127.0.0.1:8010',
      '/render': 'http://127.0.0.1:8010',
      '/source-video': 'http://127.0.0.1:8010',
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
  },
})
