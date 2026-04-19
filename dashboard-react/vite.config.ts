import { defineConfig, type ProxyOptions } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const BACKEND = 'http://localhost:8765'

const BACKEND_PATH_PREFIXES = [
  '/api',
  '/config',
  '/kb',
  '/fiction',
  '/philosophy',
  '/episteme',
  '/experiential',
  '/aesthetics',
  '/tensions',
]

const GATEWAY_SECRET = process.env.GATEWAY_SECRET

function makeProxyOptions(): ProxyOptions {
  return {
    target: BACKEND,
    changeOrigin: true,
    configure: (proxyInstance) => {
      if (!GATEWAY_SECRET) return
      proxyInstance.on('proxyReq', (req) => {
        req.setHeader('Authorization', `Bearer ${GATEWAY_SECRET}`)
      })
    },
  }
}

const proxy: Record<string, ProxyOptions> = Object.fromEntries(
  BACKEND_PATH_PREFIXES.map((prefix) => [prefix, makeProxyOptions()]),
)

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy,
  },
  base: '/cp/',
  build: {
    outDir: '../dashboard/build',
    target: ['es2020', 'safari14'],
  },
})
