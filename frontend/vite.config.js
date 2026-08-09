import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const servicePrefix = process.env.JUPYTERHUB_SERVICE_PREFIX || ''
const frontendPort = process.env.FRONTEND_PORT || 5173

// JupyterHub는 /user/.../proxy/{port}/ 앞부분을 잘라내고 Vite에 전달한다.
// 이 미들웨어가 잘려나간 앞부분을 다시 붙여줘서 Vite 내부 라우팅이 정상 동작하게 한다.
function jupyterhubPlugin(prefix, port) {
  if (!prefix) return null
  const proxyBase = `${prefix}proxy/${port}`
  return {
    name: 'jupyterhub-proxy-rewrite',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use((req, _res, next) => {
        // /api, /ws 는 Vite 프록시가 그대로 백엔드로 보내야 하므로 건드리지 않는다.
        if (req.url && !req.url.startsWith(proxyBase) &&
            !req.url.startsWith('/api') && !req.url.startsWith('/ws')) {
          req.url = proxyBase + req.url
        }
        next()
      })
    },
  }
}

export default defineConfig(({ command }) => ({
  base: command === 'build'
    ? './'
    : (servicePrefix ? `${servicePrefix}proxy/${frontendPort}/` : '/'),
  envDir: '..',
  plugins: [
    react(),
    tailwindcss(),
    jupyterhubPlugin(servicePrefix, frontendPort),
  ],
  server: {
    host: '0.0.0.0',
    allowedHosts: true,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
      },
    },
    watch: {
      usePolling: true,
      interval: 1000,
    },
  },
}))
