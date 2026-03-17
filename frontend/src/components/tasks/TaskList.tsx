import { useMemo } from 'react';
import { useTaskStore } from '@/hooks/useTaskStore';
import { deleteTask } from '@/api/tasks';
import { toast } from '@/utils/toast';
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
  const tasksMap = useTaskStore((s) => s.tasks);
  const tasks = useMemo(() => Object.values(tasksMap), [tasksMap]);
  const activeTaskId = useTaskStore((s) => s.activeTaskId);
  const selectTask = useTaskStore((s) => s.selectTask);
  const searchQuery = useTaskStore((s) => s.searchQuery);
  const statusFilter = useTaskStore((s) => s.statusFilter);
  const setSearch = useTaskStore((s) => s.setSearch);
  const setFilter = useTaskStore((s) => s.setFilter);
  const selectedIds = useTaskStore((s) => s.selectedIds);
  const toggleSelect = useTaskStore((s) => s.toggleSelect);
  const clearSelection = useTaskStore((s) => s.clearSelection);
  const bulkRemove = useTaskStore((s) => s.bulkRemove);

  const filtered = tasks
    .filter((t) => statusFilter === 'all' || t.status === statusFilter)
    .filter((t) => !searchQuery || t.task.toLowerCase().includes(searchQuery.toLowerCase()))
    .sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));

  async function handleBulkDelete() {
    const ids = [...selectedIds];
    if (!confirm(`确定删除 ${ids.length} 个任务？`)) return;
    let ok = 0;
    for (const id of ids) {
      try { await deleteTask(id); ok++; } catch { /* skip */ }
    }
    bulkRemove(ids);
    toast.success(`已删除 ${ok} 个任务`);
  }

  return (
    <div className="task-list-container">
      <div className="task-list-filters">
        <input
          type="text"
          className="task-search"
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

      {selectedIds.size > 0 && (
        <div className="bulk-bar">
          <span>已选 {selectedIds.size} 项</span>
          <button className="btn-danger" style={{ padding: '4px 10px', fontSize: 12 }} onClick={handleBulkDelete}>
            批量删除
          </button>
          <button className="btn-ghost" style={{ padding: '4px 10px', fontSize: 12 }} onClick={clearSelection}>
            取消选择
          </button>
        </div>
      )}

      <div className="task-list-scroll">
        {filtered.length === 0 ? (
          <div className="empty-state">暂无任务</div>
        ) : (
          filtered.map((task) => (
            <div
              key={task.id}
              className={`task-item ${activeTaskId === task.id ? 'active' : ''} ${selectedIds.has(task.id) ? 'selected' : ''}`}
              onClick={() => selectTask(task.id)}
            >
              <div className="task-item-header">
                <input
                  type="checkbox"
                  className="task-checkbox"
                  checked={selectedIds.has(task.id)}
                  onChange={(e) => { e.stopPropagation(); toggleSelect(task.id); }}
                  onClick={(e) => e.stopPropagation()}
                />
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
