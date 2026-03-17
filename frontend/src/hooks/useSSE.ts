import { useEffect, useRef } from 'react';
import { useTaskStore } from './useTaskStore';
import { useExploreStore } from './useExploreStore';
import { useRecordingStore } from './useRecordingStore';

export function useSSE() {
  const retryDelay = useRef(1000);

  useEffect(() => {
    let es: EventSource | null = null;
    let closed = false;

    function connect() {
      if (closed) return;
      es = new EventSource('/tasks/stream');

      es.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          retryDelay.current = 1000; // reset on success

          const ts = useTaskStore.getState();
          const es2 = useExploreStore.getState();
          const rs = useRecordingStore.getState();

          switch (msg.type) {
            case 'snapshot':
              if (msg.tasks) ts.setSnapshot(msg.tasks);
              if (msg.explore_tasks) es2.setSnapshot(msg.explore_tasks);
              break;
            case 'new_task':
              ts.addTask(msg.task);
              break;
            case 'status':
              ts.updateStatus(msg.task_id, msg.data, msg.screenshots);
              break;
            case 'log':
              ts.appendLog(msg.task_id, msg.data);
              break;
            case 'progress': {
              if (msg.current != null && msg.total != null) {
                ts.updateProgress(msg.task_id, msg.current, msg.total);
              } else if (typeof msg.data === 'string') {
                const parts = msg.data.split('/');
                if (parts.length === 2) {
                  ts.updateProgress(msg.task_id, parseInt(parts[0]), parseInt(parts[1]));
                }
              }
              break;
            }
            case 'new_screenshot':
              ts.addScreenshot(msg.task_id, msg.filename);
              break;
            case 'waiting_input':
              ts.setWaitingInput(msg.task_id, msg.question || '', msg.reason || '');
              break;
            case 'explore_new':
              es2.addTask(msg.task);
              break;
            case 'explore_status':
              es2.updateStatus(msg.eid, msg.data, msg.extra);
              break;
            case 'explore_log':
              es2.appendLog(msg.eid, msg.data);
              break;
            case 'recording_action':
              if (msg.recording_id && msg.action) {
                rs.appendAction(msg.recording_id, msg.action);
              }
              break;
          }
        } catch {
          // ignore parse errors
        }
      };

      es.onerror = () => {
        es?.close();
        if (!closed) {
          setTimeout(connect, retryDelay.current);
          retryDelay.current = Math.min(retryDelay.current * 2, 30000);
        }
      };
    }

    connect();

    return () => {
      closed = true;
      es?.close();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
