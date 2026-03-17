import { useTaskStore } from '@/hooks/useTaskStore';
import type { TaskStatus } from '@/types/task';
import './TaskList.css';

const STATUS_OPTIONS: { value: TaskStatus | 'all'; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'running', label: '运行中' },
  { value: 'done', label: '已完成' },
  { value: 'failed', label: '失败' },
  { value: 'pending', label: '等待中' },
];

export function TaskList() {
  const tasks = useTaskStore((s) => Object.values(s.tasks));
  const activeTaskId = useTaskStore((s) => s.activeTaskId);
  const selectTask = useTaskStore((s) => s.selectTask);
  const searchQuery = useTaskStore((s) => s.searchQuery);
  const statusFilter = useTaskStore((s) => s.statusFilter);
  const setSearch = useTaskStore((s) => s.setSearch);
  const setFilter = useTaskStore((s) => s.setFilter);

  const filtered = tasks
    .filter((t) => statusFilter === 'all' || t.status === statusFilter)
    .filter((t) => !searchQuery || t.task.toLowerCase().includes(searchQuery.toLowerCase()))
    .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));

  return (
    <div className="task-list-container">
      <div className="task-list-filters">
        <input
          type="text"
          placeholder="搜索任务..."
          value={searchQuery}
          onChange={(e) => setSearch(e.target.value)}
        />
        <div className="filter-chips">
          {STATUS_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              className={`chip ${statusFilter === opt.value ? 'active' : ''}`}
              onClick={() => setFilter(opt.value)}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>
      <div className="task-list-scroll">
        {filtered.length === 0 ? (
          <div className="empty-state">暂无任务</div>
        ) : (
          filtered.map((task) => (
            <div
              key={task.id}
              className={`task-item ${activeTaskId === task.id ? 'active' : ''}`}
              onClick={() => selectTask(task.id)}
            >
              <div className="task-item-header">
                <span className={`badge badge-${task.status}`}>{task.status}</span>
                <span className="task-id">{task.id.slice(0, 8)}</span>
              </div>
              <div className="task-item-text">{task.task.slice(0, 80)}</div>
              {task.progress && task.status === 'running' && (
                <div className="progress-bar">
                  <div
                    className="progress-fill"
                    style={{ width: `${(task.progress.current / task.progress.total) * 100}%` }}
                  />
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
