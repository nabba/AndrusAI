// Same-origin API calls — dashboard server proxies /api/* to the gateway.
// No CORS needed. Works in both dev (Vite proxy) and production (server.mjs proxy).
const BASE = '/api/cp';

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(options?.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`API ${res.status}: ${text.slice(0, 200)}`);
  }
  return res.json();
}
