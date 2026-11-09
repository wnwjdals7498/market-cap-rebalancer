import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// FastAPI 가 frontend/dist 를 /app/ 으로 서빙한다.
export default defineConfig({
  base: '/app/',
  plugins: [react()],
  server: {
    proxy: { '/api': 'http://127.0.0.1:8765' },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
