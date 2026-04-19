// Same-origin API calls — dashboard server proxies backend paths to the gateway.
// Callers pass the full absolute path (e.g. "/api/cp/tickets", "/kb/status",
// "/config/creative_mode"). The proxy handles forwarding to the FastAPI gateway.

export class ApiError extends Error {
  status: number;
  body: string;
  constructor(status: number, body: string) {
    super(`API ${status}: ${body.slice(0, 200)}`);
    this.status = status;
    this.body = body;
  }
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const init: RequestInit = {
    ...options,
    headers: {
      Accept: 'application/json',
      ...(options?.body && !(options.body instanceof FormData)
        ? { 'Content-Type': 'application/json' }
        : {}),
      ...(options?.headers || {}),
    },
  };
  const res = await fetch(path, init);
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new ApiError(res.status, text);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json();
}
