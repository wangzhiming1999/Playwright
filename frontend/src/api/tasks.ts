import { api } from './client';

export function submitTasks(tasks: string[], browserMode = 'builtin', cdpUrl?: string, chromeProfile?: string) {
  return api.post<{ task_ids: string[] }>('/run', {
    tasks,
    browser_mode: browserMode,
    cdp_url: cdpUrl,
    chrome_profile: chromeProfile,
  });
}

export function cancelTask(taskId: string) {
  return api.post<{ status: string }>(`/tasks/${taskId}/cancel`);
}

export function deleteTask(taskId: string) {
  return api.del<{ status: string }>(`/tasks/${taskId}`);
}

export function replyToTask(taskId: string, answer: string) {
  return api.post<{ status: string }>(`/tasks/${taskId}/reply?answer=${encodeURIComponent(answer)}`);
}

export function getCuration(taskId: string) {
  return api.get<unknown>(`/tasks/${taskId}/curation`);
}

export function runCuration(taskId: string, productContext?: string) {
  return api.post<unknown>('/curate', { task_id: taskId, product_context: productContext });
}

export function getGenerated(taskId: string) {
  return api.get<unknown>(`/tasks/${taskId}/generated`);
}

export function runGenerate(source: string, sourceId: string) {
  return api.post<unknown>('/generate', { source, source_id: sourceId });
}

export function editGenerated(source: string, sourceId: string, field: string, value: string) {
  return api.patch<unknown>('/generate/edit', { source, source_id: sourceId, field, value });
}

export function cleanup(keepLast = 20) {
  return api.post<{ deleted_tasks: number }>('/cleanup', { keep_last: keepLast });
}
