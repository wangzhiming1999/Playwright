import { api } from './client';

export interface Memory {
  id: string;
  memory_type: 'site' | 'pattern' | 'failure';
  domain: string;
  title: string;
  content: Record<string, unknown>;
  source_task_id: string | null;
  hit_count: number;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface MemoryStats {
  total: number;
  by_type: Record<string, number>;
  top_domains: { domain: string; count: number }[];
}

export const listMemories = (domain?: string, type?: string) => {
  const params = new URLSearchParams();
  if (domain) params.set('domain', domain);
  if (type) params.set('type', type);
  const qs = params.toString();
  return api.get<Memory[]>(`/memories${qs ? '?' + qs : ''}`);
};

export const getMemoryStats = () => api.get<MemoryStats>('/memories/stats');

export const getMemory = (id: string) => api.get<Memory>(`/memories/${id}`);

export const updateMemory = (id: string, data: { title?: string; content?: string }) =>
  api.put<{ ok: boolean }>(`/memories/${id}`, data);

export const deleteMemory = (id: string) => api.del<{ ok: boolean }>(`/memories/${id}`);

export const batchDeleteMemories = (ids: string[]) =>
  api.post<{ deleted: number }>('/memories/batch-delete', { ids });
