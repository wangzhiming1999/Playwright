import { useEffect } from 'react';
import { useTaskStore } from './useTaskStore';

export function useSSE() {
  useEffect(() => {
    const apiKey = localStorage.getItem('api_key') || '';
    const url = apiKey ? `/tasks/stream?api_key=${encodeURIComponent(apiKey)}` : '/tasks/stream';
    const es = new EventSource(url);

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        const ts = useTaskStore.getState();

        if (data.type === 'snapshot') {
          ts.setSnapshot(data.tasks ?? []);
        } else if (data.type === 'new_task') {
          ts.addTask(data.task);
        } else if (data.type === 'log') {
          ts.appendLog(data.task_id, data.data);
        } else if (data.type === 'status') {
          ts.updateStatus(data.task_id, data.data, data.screenshots);
        } else if (data.type === 'new_screenshot') {
          ts.addScreenshot(data.task_id, data.filename);
        } else if (data.type === 'progress') {
          ts.updateProgress(data.task_id, data.current, data.total);
        } else if (data.type === 'waiting_input') {
          ts.setWaitingInput(data.task_id, data.question, data.reason);
        }
      } catch {
        // ignore parse errors
      }
    };

    es.onerror = () => {
      // EventSource auto-reconnects on error
    };

    return () => {
      es.close();
    };
  }, []);
}
