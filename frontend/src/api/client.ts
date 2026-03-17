const BASE = '';

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const apiKey = localStorage.getItem('api_key') || '';
  const headers: Record<string, string> = { ...(init?.headers as Record<string, string>) };
  if (apiKey) headers['X-API-Key'] = apiKey;
  if (init?.body && typeof init.body === 'string') headers['Content-Type'] = 'application/json';

  const res = await fetch(`${BASE}${url}`, { ...init, headers });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json();
}

export const api = {
  get: <T>(url: string) => request<T>(url),
  post: <T>(url: string, body?: unknown) => request<T>(url, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  put: <T>(url: string, body?: unknown) => request<T>(url, { method: 'PUT', body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(url: string, body?: unknown) => request<T>(url, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined }),
  del: <T>(url: string) => request<T>(url, { method: 'DELETE' }),
};
