import { useEffect, useRef } from 'react';
import { useTaskStore } from './useTaskStore';
import { useExploreStore } from './useExploreStore';

export function useSSE() {
  const taskStore = useTaskStore();
  const exploreStore = useExploreStore();
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

          switch (msg.type) {
            case 'snapshot':
              if (msg.tasks) taskStore.setSnapshot(msg.tasks);
              if (msg.explore_tasks) exploreStore.setSnapshot(msg.explore_tasks);
              break;
            case 'new_task':
              taskStore.addTask(msg.task);
              break;
            case 'status':
              taskStore.updateStatus(msg.task_id, msg.data, msg.screenshots);
              break;
            case 'log':
              taskStore.appendLog(msg.task_id, msg.data);
              break;
            case 'progress': {
              const parts = msg.data?.split('/');
              if (parts?.length === 2) {
                taskStore.updateProgress(msg.task_id, parseInt(parts[0]), parseInt(parts[1]));
              }
              break;
            }
            case 'new_screenshot':
              taskStore.addScreenshot(msg.task_id, msg.filename);
              break;
            case 'waiting_input':
              taskStore.setWaitingInput(msg.task_id, msg.question || '', msg.reason || '');
              break;
            case 'explore_new':
              exploreStore.addTask(msg.task);
              break;
            case 'explore_status':
              exploreStore.updateStatus(msg.eid, msg.data, msg.extra);
              break;
            case 'explore_log':
              exploreStore.appendLog(msg.eid, msg.data);
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
