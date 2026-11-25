import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const proxy = {
  '/api': {
    target: process.env.API_PROXY_TARGET || 'http://127.0.0.1:8010',
    ws: true,
  },
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 3000, strictPort: true, proxy },
  preview: { port: 3000, strictPort: true, proxy },
})
