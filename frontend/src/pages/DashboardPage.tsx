import { useTaskStore } from '@/hooks/useTaskStore';
import './DashboardPage.css';

export function DashboardPage() {
  const tasks = useTaskStore((s) => Object.values(s.tasks));

  const total = tasks.length;
  const running = tasks.filter((t) => t.status === 'running').length;
  const done = tasks.filter((t) => t.status === 'done').length;
  const failed = tasks.filter((t) => t.status === 'failed').length;
  const successRate = total > 0 ? Math.round((done / total) * 100) : 0;

  const recent = tasks
    .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
    .slice(0, 10);

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>Dashboard</h1>
      </header>

      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-label">总任务数</div>
          <div className="stat-value">{total}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">运行中</div>
          <div className="stat-value" style={{ color: 'var(--accent)' }}>{running}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">已完成</div>
          <div className="stat-value" style={{ color: 'var(--green)' }}>{done}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">失败</div>
          <div className="stat-value" style={{ color: 'var(--red)' }}>{failed}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">成功率</div>
          <div className="stat-value">{successRate}%</div>
        </div>
      </div>

      <div className="recent-section">
        <h2>最近任务</h2>
        {recent.length === 0 ? (
          <div className="empty-state">暂无任务</div>
        ) : (
          <div className="recent-list">
            {recent.map((task) => (
              <div key={task.id} className="recent-item">
                <span className={`badge badge-${task.status}`}>{task.status}</span>
                <span className="recent-task">{task.task.slice(0, 60)}</span>
                <span className="recent-time">{task.created_at?.slice(0, 19) || ''}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
