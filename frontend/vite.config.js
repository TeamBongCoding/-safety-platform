import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  // 상대 경로로 빌드하면 JupyterHub의 /user/.../proxy/<port>/ 아래에서도
  // JS와 CSS 파일을 현재 프록시 경로에서 불러올 수 있다.
  base: './',
  envDir: '..',
  plugins: [react(), tailwindcss()],
  server: {
    host: '0.0.0.0',
    allowedHosts: 'all',
    watch: {
      usePolling: true,
      interval: 1000,
    },
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
      },
    },
  },
})
