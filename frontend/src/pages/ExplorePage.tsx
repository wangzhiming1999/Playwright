import { useState, useMemo } from 'react';
import { useExploreStore } from '@/hooks/useExploreStore';
import { startExplore, deleteExplore, curateExplore } from '@/api/explore';
import { runGenerate } from '@/api/tasks';
import { toast } from '@/utils/toast';
import { normalizeCurationResult, normalizeGenerated } from '@/utils/normalize';
import { CurationView } from '@/components/CurationView';
import { GeneratedView } from '@/components/GeneratedView';
import { LogViewer } from '@/components/LogViewer';
import { ScreenshotReplay } from '@/components/ScreenshotReplay';
import './ExplorePage.css';

export function ExplorePage() {
  const tasksMap = useExploreStore((s) => s.tasks);
  const tasks = useMemo(() => Object.values(tasksMap), [tasksMap]);
  const activeEid = useExploreStore((s) => s.activeEid);
  const selectTask = useExploreStore((s) => s.selectTask);
  const removeTask = useExploreStore((s) => s.removeTask);
  const setCuration = useExploreStore((s) => s.setCuration);
  const setGenerated = useExploreStore((s) => s.setGenerated);
  const activeTask = useExploreStore((s) => activeEid ? s.tasks[activeEid] : null);

  const [url, setUrl] = useState('');
  const [context, setContext] = useState('');
  const [maxPages, setMaxPages] = useState(12);
  const [loading, setLoading] = useState(false);
  const [curating, setCurating] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [tab, setTab] = useState<'logs' | 'screenshots' | 'site' | 'curation' | 'generated'>('logs');

  async function handleSubmit() {
    if (!url.trim()) return;
    setLoading(true);
    try {
      await startExplore(url, context || undefined, maxPages);
      setUrl('');
      toast.success('探索任务已启动');
    } catch (e) { toast.error(`启动失败: ${e instanceof Error ? e.message : String(e)}`); }
    setLoading(false);
  }

  async function handleDelete(eid: string) {
    if (!confirm('确定删除？')) return;
    try { await deleteExplore(eid); removeTask(eid); toast.success('已删除'); } catch (e) { toast.error(e instanceof Error ? e.message : String(e)); }
  }

  async function handleCurate() {
    if (!activeTask) return;
    setCurating(true);
    try {
      const res = await curateExplore(activeTask.eid);
      setCuration(activeTask.eid, normalizeCurationResult(res));
      toast.success('策展完成');
    } catch (e) { toast.error(`策展失败: ${e instanceof Error ? e.message : String(e)}`); }
    setCurating(false);
  }

  async function handleGenerate() {
    if (!activeTask) return;
    setGenerating(true);
    try {
      const res = await runGenerate('explore', activeTask.eid);
      setGenerated(activeTask.eid, normalizeGenerated(res));
      toast.success('内容生成完成');
    } catch (e) { toast.error(`生成失败: ${e instanceof Error ? e.message : String(e)}`); }
    setGenerating(false);
  }

  const sorted = [...tasks].sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));

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
              <button className={`tab-btn ${tab === 'curation' ? 'active' : ''}`} onClick={() => setTab('curation')}>
                策展 {activeTask.curation && `(${activeTask.curation.cards.length})`}
              </button>
              <button className={`tab-btn ${tab === 'generated' ? 'active' : ''}`} onClick={() => setTab('generated')}>生成内容</button>
            </div>

            <div className="tab-content">
              {tab === 'logs' && (
                <LogViewer logs={activeTask.logs} isLive={activeTask.status === 'running'} />
              )}

              {tab === 'screenshots' && (
                <ScreenshotReplay
                  screenshots={activeTask.screenshots.map((f) => typeof f === 'string' ? f : f.filename)}
                  screenshotPrefix={`/screenshots/explore_${activeTask.eid}`}
                  logs={activeTask.logs}
                />
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
                    {activeTask.visited_pages && activeTask.visited_pages.length > 0 && (
                      <div style={{ marginTop: 16 }}>
                        <h3 style={{ fontSize: 14, marginBottom: 8 }}>已访问页面 ({activeTask.visited_pages.length})</h3>
                        <div className="visited-pages-table">
                          <div className="visited-row visited-header">
                            <span>页面</span><span>类型</span><span>分数</span>
                          </div>
                          {activeTask.visited_pages.map((p, i) => (
                            <div key={i} className="visited-row">
                              <span className="visited-url" title={p.url}>{p.title || p.url}</span>
                              <span className="visited-type">{p.page_type}</span>
                              <span className={`badge ${p.score >= 7 ? 'badge-done' : p.score >= 5 ? 'badge-running' : 'badge-failed'}`}>{p.score}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ) : <div className="empty-state">暂无网站理解数据</div>
              )}

              {tab === 'curation' && (
                activeTask.curation ? (
                  <CurationView curation={activeTask.curation} screenshotPrefix={`/screenshots/explore_${activeTask.eid}`} />
                ) : activeTask.status === 'done' ? (
                  <div className="empty-state">
                    <button className="btn-primary" onClick={handleCurate} disabled={curating}>
                      {curating ? '策展中...' : '运行策展'}
                    </button>
                  </div>
                ) : (
                  <div className="empty-state">任务完成后可运行策展</div>
                )
              )}

              {tab === 'generated' && (
                activeTask.generated ? (
                  <GeneratedView generated={activeTask.generated} source="explore" sourceId={activeTask.eid} />
                ) : activeTask.curation ? (
                  <div className="empty-state">
                    <button className="btn-primary" onClick={handleGenerate} disabled={generating}>
                      {generating ? '生成中...' : '生成内容'}
                    </button>
                  </div>
                ) : (
                  <div className="empty-state">请先运行策展</div>
                )
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
