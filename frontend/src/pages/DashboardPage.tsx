import { useEffect, useMemo, useState } from 'react';
import { useTaskStore } from '@/hooks/useTaskStore';
import { useExploreStore } from '@/hooks/useExploreStore';
import { getPool } from '@/api/tasks';
import { DonutChart } from '@/components/charts/DonutChart';
import { BarChart } from '@/components/charts/BarChart';
import { TimelineChart } from '@/components/charts/TimelineChart';
import '@/components/charts/charts.css';
import './DashboardPage.css';

export function DashboardPage() {
  const tasksMap = useTaskStore((s) => s.tasks);
  const tasks = useMemo(() => Object.values(tasksMap), [tasksMap]);
  const exploreMap = useExploreStore((s) => s.tasks);
  const explores = useMemo(() => Object.values(exploreMap), [exploreMap]);

  // Pool 状态
  const [pool, setPool] = useState({ max_workers: 0, running: 0, queued: 0, completed: 0, failed: 0 });
  useEffect(() => {
    const fetchPool = () => getPool().then(setPool).catch(() => {});
    fetchPool();
    const timer = setInterval(fetchPool, 10000);
    return () => clearInterval(timer);
  }, []);

  const total = tasks.length;
  const pending = tasks.filter((t) => t.status === 'pending').length;
  const running = tasks.filter((t) => t.status === 'running').length;
  const done = tasks.filter((t) => t.status === 'done').length;
  const failed = tasks.filter((t) => t.status === 'failed').length;
  const successRate = total > 0 ? Math.round((done / total) * 100) : 0;

  const expTotal = explores.length;
  const expRunning = explores.filter((t) => t.status === 'running').length;
  const expDone = explores.filter((t) => t.status === 'done').length;

  // Status distribution bar percentages
  const pctDone = total > 0 ? (done / total) * 100 : 0;
  const pctRunning = total > 0 ? (running / total) * 100 : 0;
  const pctFailed = total > 0 ? (failed / total) * 100 : 0;
  const pctPending = total > 0 ? (pending / total) * 100 : 0;

  // Chart data
  const statusSegments = useMemo(() => [
    { value: done, color: 'var(--green)', label: '完成' },
    { value: running, color: 'var(--accent)', label: '运行中' },
    { value: pending, color: 'var(--text-muted)', label: '等待' },
    { value: failed, color: 'var(--red)', label: '失败' },
  ], [done, running, pending, failed]);

  const dailyStats = useMemo(() => {
    const map: Record<string, { success: number; failed: number }> = {};
    for (const t of tasks) {
      const date = (t.created_at || '').slice(0, 10);
      if (!date) continue;
      if (!map[date]) map[date] = { success: 0, failed: 0 };
      if (t.status === 'done') map[date].success++;
      else if (t.status === 'failed') map[date].failed++;
    }
    return Object.entries(map)
      .sort(([a], [b]) => a.localeCompare(b))
      .slice(-14)
      .map(([date, v]) => ({ date, ...v }));
  }, [tasks]);

  const durationBuckets = useMemo(() => {
    const buckets = [
      { label: '<30s', max: 30, count: 0 },
      { label: '30s-2m', max: 120, count: 0 },
      { label: '2-5m', max: 300, count: 0 },
      { label: '5m+', max: Infinity, count: 0 },
    ];
    for (const t of tasks) {
      if (!t.started_at || !t.finished_at) continue;
      const dur = (new Date(t.finished_at).getTime() - new Date(t.started_at).getTime()) / 1000;
      if (isNaN(dur) || dur < 0) continue;
      const bucket = buckets.find((b) => dur < b.max) || buckets[buckets.length - 1];
      bucket.count++;
    }
    return buckets.map((b) => ({ label: b.label, value: b.count }));
  }, [tasks]);

  // Combined recent activity
  const recentActivity = useMemo(() => {
    const items: { type: 'task' | 'explore'; id: string; label: string; status: string; time: string }[] = [];
    tasks.forEach(t => items.push({ type: 'task', id: t.id, label: t.task.slice(0, 60), status: t.status, time: t.created_at || '' }));
    explores.forEach(e => items.push({ type: 'explore', id: e.eid, label: e.url, status: e.status, time: e.created_at || '' }));
    return items.sort((a, b) => b.time.localeCompare(a.time)).slice(0, 15);
  }, [tasks, explores]);

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
        <div className="stat-card">
          <div className="stat-label">探索任务</div>
          <div className="stat-value">{expTotal}</div>
          <div className="stat-sub">{expRunning} 运行中 / {expDone} 完成</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">任务池</div>
          <div className="stat-value">{pool.running}<span style={{ fontSize: 14, fontWeight: 400, color: 'var(--text-secondary)' }}>/{pool.max_workers}</span></div>
          <div className="stat-sub">{pool.queued} 排队 / {pool.completed} 已完成</div>
        </div>
      </div>

      {/* Status distribution bar */}
      {total > 0 && (
        <div className="status-bar-section">
          <h2>状态分布</h2>
          <div className="status-bar">
            {pctDone > 0 && <div className="status-seg seg-done" style={{ width: `${pctDone}%` }} title={`完成 ${done}`} />}
            {pctRunning > 0 && <div className="status-seg seg-running" style={{ width: `${pctRunning}%` }} title={`运行中 ${running}`} />}
            {pctPending > 0 && <div className="status-seg seg-pending" style={{ width: `${pctPending}%` }} title={`等待 ${pending}`} />}
            {pctFailed > 0 && <div className="status-seg seg-failed" style={{ width: `${pctFailed}%` }} title={`失败 ${failed}`} />}
          </div>
          <div className="status-legend">
            <span><i className="dot dot-done" /> 完成 {done}</span>
            <span><i className="dot dot-running" /> 运行中 {running}</span>
            <span><i className="dot dot-pending" /> 等待 {pending}</span>
            <span><i className="dot dot-failed" /> 失败 {failed}</span>
          </div>
        </div>
      )}

      {/* Charts */}
      <div className="charts-grid">
        <div className="chart-card">
          <h3>任务状态分布</h3>
          <DonutChart segments={statusSegments} />
        </div>
        <div className="chart-card">
          <h3>每日完成趋势</h3>
          <TimelineChart data={dailyStats} />
        </div>
        <div className="chart-card">
          <h3>任务耗时分布</h3>
          <BarChart data={durationBuckets} />
        </div>
      </div>

      <div className="recent-section">
        <h2>最近活动</h2>
        {recentActivity.length === 0 ? (
          <div className="empty-state">暂无活动</div>
        ) : (
          <div className="recent-list">
            {recentActivity.map((item) => (
              <div key={`${item.type}-${item.id}`} className="recent-item">
                <span className="recent-type-badge">{item.type === 'task' ? '任务' : '探索'}</span>
                <span className={`badge badge-${item.status}`}>{item.status}</span>
                <span className="recent-task">{item.label}</span>
                <span className="recent-time">{item.time}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
