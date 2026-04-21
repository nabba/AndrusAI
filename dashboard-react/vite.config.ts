import { defineConfig, type ProxyOptions } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import fs from 'node:fs'
import path from 'node:path'

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

// Load GATEWAY_SECRET from the sibling .env if it isn't already in the
// process environment. Without it every write to /config/* and
// /budgets/override comes back 401 because the FastAPI gateway expects
// `Authorization: Bearer <secret>`. Mirrors the fallback dashboard/
// server.mjs already does for the production static server.
function loadGatewaySecret(): string {
  if (process.env.GATEWAY_SECRET) return process.env.GATEWAY_SECRET
  const envPath = path.resolve(__dirname, '..', '.env')
  try {
    const text = fs.readFileSync(envPath, 'utf8')
    const match = text.match(/^\s*GATEWAY_SECRET\s*=\s*(.+?)\s*$/m)
    if (match) {
      return match[1].replace(/^(['"])(.*)\1$/, '$2')
    }
  } catch {
    // .env missing / unreadable — fall through.
  }
  return ''
}

const GATEWAY_SECRET = loadGatewaySecret()

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
