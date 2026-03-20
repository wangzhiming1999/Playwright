import { api } from './client';

export interface RecordedAction {
  type: string;
  timestamp: number;
  url: string;
  selector: string;
  text: string;
  tag: string;
  input_type?: string;
  meta?: Record<string, unknown>;
}

export interface Recording {
  id: string;
  title: string;
  start_url: string;
  actions: RecordedAction[];
  parameters: { key: string; type: string; description: string; default_value: string }[];
  workflow_id: string | null;
  status: 'recording' | 'completed' | 'converted';
  created_at: string;
}

export const startRecording = (data: { title?: string; start_url?: string; browser_mode?: string; cdp_url?: string }) =>
  api.post<{ recording_id: string; status: string }>('/recordings/start', data);

export const stopRecording = (id: string) =>
  api.post<{ recording_id: string; actions: RecordedAction[]; parameters: Recording['parameters']; status: string }>(`/recordings/${id}/stop`);

export const listRecordings = () => api.get<Recording[]>('/recordings');

export const getRecording = (id: string) => api.get<Recording>(`/recordings/${id}`);

export const deleteRecording = (id: string) => api.del<{ ok: boolean }>(`/recordings/${id}`);

export const convertRecording = (id: string, data?: { title?: string; parameters?: Record<string, unknown>[] }) =>
  api.post<{ workflow_id: string; yaml_content: string }>(`/recordings/${id}/convert`, data || {});

export const replayRecording = (id: string, parameters?: Record<string, unknown>) =>
  api.post<{ run_id: string; workflow_id: string }>(`/recordings/${id}/replay`, { parameters: parameters || {} });

export const deleteRecordingAction = (id: string, index: number) =>
  api.del<{ ok: boolean; actions_count: number }>(`/recordings/${id}/actions/${index}`);

export const updateRecordingAction = (id: string, index: number, data: { text?: string; selector?: string }) =>
  api.put<{ ok: boolean }>(`/recordings/${id}/actions/${index}`, data);

export const replaceRecordingActions = (id: string, actions: RecordedAction[]) =>
  api.put<{ ok: boolean; actions_count: number }>(`/recordings/${id}/actions`, { actions });

export const previewRecordingConvert = (id: string) =>
  api.post<{
    original_count: number;
    cleaned_count: number;
    parameters: Recording['parameters'];
    yaml_preview: string;
  }>(`/recordings/${id}/preview`, {});
