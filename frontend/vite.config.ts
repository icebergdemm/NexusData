import path from "node:path"
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig(({ command, isPreview }) => ({
  // During local development Vite must accept both SPA entry points. Built
  // assets intentionally stay below /admin/assets so production can serve
  // one immutable bundle to the classic and opt-in V2 shells.
  base: command === 'serve' && !isPreview ? '/' : '/admin/',
  plugins: [react(), tailwindcss()],
  define: {
    // Отпечаток сборки. Нужен для cache-busting hand-written скриптов из
    // /static/js/, которые подключаются <script src> в обход бандлера и
    // потому не получают контентного хэша в имени (см. pages/replays/
    // player.tsx). Меняется при каждой сборке — новый деплой = новый URL =
    // браузер гарантированно берёт свежий файл, а не копию из кэша.
    __BUILD_ID__: JSON.stringify(Date.now().toString(36)),
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/adminold': 'http://localhost:8000',
      '/static': 'http://localhost:8000',
      '/login': 'http://localhost:8000',
      '/auth': 'http://localhost:8000',
      '/logout': 'http://localhost:8000',
    },
  },
}))
