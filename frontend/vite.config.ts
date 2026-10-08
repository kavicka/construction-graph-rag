import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    // mobil na stavbe cez `tailscale serve` (HTTPS na *.ts.net → localhost:5173)
    allowedHosts: ['.ts.net'],
    proxy: {
      // RAG_API = backend mimo localhost:8000 (Docker, druhá inštancia)
      '/api': process.env.RAG_API ?? 'http://localhost:8000',
      '/mcp': process.env.RAG_API ?? 'http://localhost:8000',
    },
  },
})
