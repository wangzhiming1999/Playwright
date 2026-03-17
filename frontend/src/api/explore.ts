import { api } from './client';

export function startExplore(url: string, productContext?: string, maxPages = 12, cookiesPath?: string) {
  return api.post<{ eid: string }>('/explore', {
    url,
    product_context: productContext,
    max_pages: maxPages,
    cookies_path: cookiesPath,
  });
}

export function deleteExplore(eid: string) {
  return api.del<{ status: string }>(`/explore/${eid}`);
}

export function curateExplore(eid: string, productContext?: string) {
  return api.post<unknown>(`/explore/${eid}/curate`, { product_context: productContext });
}
