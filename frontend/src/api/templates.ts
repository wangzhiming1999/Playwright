import { api } from './client';
import type { Template, TemplateCategory } from '@/types/template';

export function listTemplates(category?: string) {
  const q = category ? `?category=${category}` : '';
  return api.get<Template[]>(`/templates${q}`);
}

export function listCategories() {
  return api.get<TemplateCategory[]>('/templates/categories');
}

export function getTemplate(id: string) {
  return api.get<Template>(`/templates/${id}`);
}

export function instantiateTemplate(id: string) {
  return api.post<{ id: string; title: string }>(`/templates/${id}/instantiate`);
}

export function runTemplate(id: string, parameters: Record<string, unknown>) {
  return api.post<{ run_id: string; workflow_id: string }>(`/templates/${id}/run`, { parameters });
}
