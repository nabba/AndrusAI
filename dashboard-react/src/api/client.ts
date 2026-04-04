// In production, API is on the gateway (port 8765). In dev, Vite proxies it.
const BASE = import.meta.env.DEV ? '/api/cp' : `${window.location.protocol}//${window.location.hostname}:8765/api/cp`;

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}
