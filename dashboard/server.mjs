/**
 * Lightweight dashboard server: static files + API proxy.
 * Serves the React build from serve-root/ and proxies /api/* to the gateway.
 * This eliminates CORS issues (same-origin requests).
 */
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const STATIC_ROOT = path.join(__dirname, 'serve-root');
const GATEWAY = 'http://127.0.0.1:8765';
const PORT = parseInt(process.env.PORT || '3100');

const MIME_TYPES = {
  '.html': 'text/html',
  '.js': 'application/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
};

function serveStatic(req, res) {
  let filePath = path.join(STATIC_ROOT, req.url === '/cp/' ? '/cp/index.html' : req.url);

  // SPA fallback: if file doesn't exist and path starts with /cp/, serve index.html
  if (!fs.existsSync(filePath) && req.url.startsWith('/cp/')) {
    filePath = path.join(STATIC_ROOT, 'cp', 'index.html');
  }

  if (!fs.existsSync(filePath)) {
    res.writeHead(404);
    res.end('Not found');
    return;
  }

  const ext = path.extname(filePath);
  const contentType = MIME_TYPES[ext] || 'application/octet-stream';

  const content = fs.readFileSync(filePath);
  res.writeHead(200, { 'Content-Type': contentType, 'Cache-Control': 'public, max-age=3600' });
  res.end(content);
}

function proxyToGateway(req, res) {
  const options = {
    hostname: '127.0.0.1',
    port: 8765,
    path: req.url,
    method: req.method,
    headers: { ...req.headers, host: '127.0.0.1:8765' },
  };

  const proxyReq = http.request(options, (proxyRes) => {
    res.writeHead(proxyRes.statusCode, proxyRes.headers);
    proxyRes.pipe(res, { end: true });
  });

  proxyReq.on('error', (err) => {
    console.error(`Proxy error: ${err.message}`);
    res.writeHead(502);
    res.end(JSON.stringify({ error: 'Gateway unavailable' }));
  });

  // Set a timeout (120s for large file uploads up to 20MB)
  proxyReq.setTimeout(120000, () => {
    proxyReq.destroy();
    res.writeHead(504);
    res.end(JSON.stringify({ error: 'Gateway timeout' }));
  });

  req.pipe(proxyReq, { end: true });
}

const server = http.createServer((req, res) => {
  // API requests → proxy to gateway
  if (req.url.startsWith('/api/') ||
      req.url.startsWith('/fiction/') ||
      req.url.startsWith('/philosophy/') ||
      req.url.startsWith('/kb/')) {
    proxyToGateway(req, res);
    return;
  }

  // Everything else → static files
  serveStatic(req, res);
});

server.listen(PORT, () => {
  console.log(`Dashboard server on http://localhost:${PORT}/cp/`);
  console.log(`API proxy → ${GATEWAY}/api/*`);
});
