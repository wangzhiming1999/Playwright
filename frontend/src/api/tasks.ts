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

export function retryTask(taskId: string) {
  return api.post<{ task_id: string; retry_of: string }>(`/tasks/${taskId}/retry`);
}

export function getPool() {
  return api.get<{ max_workers: number; running: number; queued: number; completed: number; failed: number }>('/pool');
}

export function resizePool(maxWorkers: number) {
  return api.put<{ old_max_workers: number; new_max_workers: number }>('/pool', { max_workers: maxWorkers });
}

// ── 浏览器池 API ──

export interface BrowserSlotInfo {
  index: number;
  in_use: boolean;
  task_id: string | null;
  connected: boolean;
  idle_seconds: number;
}

export interface BrowserPoolStats {
  enabled: boolean;
  max_size?: number;
  total?: number;
  in_use?: number;
  idle?: number;
  headless?: boolean;
  idle_timeout?: number;
  slots?: BrowserSlotInfo[];
}

export function getBrowserPool() {
  return api.get<BrowserPoolStats>('/browser-pool');
}

export function resizeBrowserPool(maxSize: number) {
  return api.put<BrowserPoolStats & { old_max_size: number; new_max_size: number }>('/browser-pool', { max_size: maxSize });
}

export function warmupBrowserPool() {
  return api.post<BrowserPoolStats & { status: string }>('/browser-pool/warmup');
}

export function batchDeleteTasks(taskIds: string[]) {
  return api.post<{ deleted: number; deleted_ids: string[] }>('/tasks/batch-delete', { task_ids: taskIds });
}
