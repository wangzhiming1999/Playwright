import { useState } from 'react';
import { useExploreStore } from '@/hooks/useExploreStore';
import { startExplore, deleteExplore } from '@/api/explore';
import './ExplorePage.css';

export function ExplorePage() {
  const tasks = useExploreStore((s) => Object.values(s.tasks));
  const activeEid = useExploreStore((s) => s.activeEid);
  const selectTask = useExploreStore((s) => s.selectTask);
  const removeTask = useExploreStore((s) => s.removeTask);
  const activeTask = useExploreStore((s) => activeEid ? s.tasks[activeEid] : null);

  const [url, setUrl] = useState('');
  const [context, setContext] = useState('');
  const [maxPages, setMaxPages] = useState(12);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<'logs' | 'screenshots' | 'site'>('logs');

  async function handleSubmit() {
    if (!url.trim()) return;
    setLoading(true);
    try {
      await startExplore(url, context || undefined, maxPages);
      setUrl('');
    } catch (e) { console.error(e); }
    setLoading(false);
  }

  async function handleDelete(eid: string) {
    if (!confirm('确定删除？')) return;
    try { await deleteExplore(eid); removeTask(eid); } catch (e) { console.error(e); }
  }

  const sorted = tasks.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));

  return (
    <div className="explore-page">
      <div className="explore-sidebar">
        <div className="explore-form">
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="输入网站 URL..." />
          <input value={context} onChange={(e) => setContext(e.target.value)} placeholder="产品上下文（可选）" />
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>最大页数</label>
            <input type="number" value={maxPages} onChange={(e) => setMaxPages(Number(e.target.value))} min={1} max={30} style={{ width: 80 }} />
            <button className="btn-primary" onClick={handleSubmit} disabled={loading || !url.trim()} style={{ flex: 1 }}>
              {loading ? <span className="spinner" /> : '开始探索'}
            </button>
          </div>
        </div>

        <div className="explore-list">
          {sorted.length === 0 ? (
            <div className="empty-state">暂无探索任务</div>
          ) : sorted.map((t) => (
            <div key={t.eid} className={`task-item ${activeEid === t.eid ? 'active' : ''}`} onClick={() => selectTask(t.eid)}>
              <div className="task-item-header">
                <span className={`badge badge-${t.status}`}>{t.status}</span>
                <span className="task-id">{t.eid.slice(0, 8)}</span>
                <button className="btn-ghost" onClick={(e) => { e.stopPropagation(); handleDelete(t.eid); }} style={{ marginLeft: 'auto', padding: '2px 6px', fontSize: 11 }}>删除</button>
              </div>
              <div className="task-item-text">{t.url}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="explore-detail">
        {!activeTask ? (
          <div className="empty-state" style={{ height: '100%' }}>选择一个探索任务查看详情</div>
        ) : (
          <>
            <div className="task-detail-header">
              <span className={`badge badge-${activeTask.status}`}>{activeTask.status}</span>
              <span className="task-detail-title">{activeTask.url}</span>
            </div>

            <div className="tab-bar">
              <button className={`tab-btn ${tab === 'logs' ? 'active' : ''}`} onClick={() => setTab('logs')}>日志</button>
              <button className={`tab-btn ${tab === 'screenshots' ? 'active' : ''}`} onClick={() => setTab('screenshots')}>
                截图 {activeTask.screenshots.length > 0 && `(${activeTask.screenshots.length})`}
              </button>
              <button className={`tab-btn ${tab === 'site' ? 'active' : ''}`} onClick={() => setTab('site')}>网站理解</button>
            </div>

            <div className="tab-content">
              {tab === 'logs' && (
                <div className="log-viewer">
                  {activeTask.logs.map((line, i) => <div key={i} className="log-line">{line}</div>)}
                </div>
              )}
              {tab === 'screenshots' && (
                activeTask.screenshots.length === 0 ? <div className="empty-state">暂无截图</div> : (
                  <div className="screenshot-grid">
                    {activeTask.screenshots.map((f) => (
                      <div key={f} className="screenshot-card">
                        <img src={`/screenshots/${activeTask.eid}/${f}`} alt={f} loading="lazy" />
                        <div className="screenshot-card-name">{f}</div>
                      </div>
                    ))}
                  </div>
                )
              )}
              {tab === 'site' && (
                activeTask.site_understanding ? (
                  <div className="site-understanding">
                    <div className="site-info-row"><span className="site-label">站点名称</span><span>{activeTask.site_understanding.site_name}</span></div>
                    <div className="site-info-row"><span className="site-label">分类</span><span>{activeTask.site_understanding.category}</span></div>
                    <div className="site-info-row"><span className="site-label">需要登录</span><span>{activeTask.site_understanding.login_required ? '是' : '否'}</span></div>
                    <div className="site-info-row"><span className="site-label">探索策略</span><span>{activeTask.site_understanding.strategy}</span></div>
                    {activeTask.site_understanding.candidate_pages?.length > 0 && (
                      <div style={{ marginTop: 16 }}>
                        <h3 style={{ fontSize: 14, marginBottom: 8 }}>候选页面</h3>
                        {activeTask.site_understanding.candidate_pages.map((p, i) => (
                          <div key={i} className="candidate-page">
                            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                              <span style={{ fontWeight: 500 }}>{p.title}</span>
                              <span className={`badge ${p.score >= 7 ? 'badge-done' : p.score >= 5 ? 'badge-running' : 'badge-failed'}`}>{p.score}</span>
                            </div>
                            <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{p.url}</div>
                            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>{p.reason}</div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ) : <div className="empty-state">暂无网站理解数据</div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
