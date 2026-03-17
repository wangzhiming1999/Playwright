import { useState, useEffect } from 'react';
import { listWorkflows, createWorkflow, deleteWorkflow, runWorkflow, listWorkflowRuns } from '@/api/workflows';
import type { Workflow, WorkflowRun } from '@/types/workflow';
import './WorkflowsPage.css';

export function WorkflowsPage() {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [yaml, setYaml] = useState('');
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<'editor' | 'runs'>('editor');

  useEffect(() => { loadWorkflows(); }, []);

  async function loadWorkflows() {
    try { setWorkflows(await listWorkflows()); } catch (e) { console.error(e); }
  }

  async function loadRuns(wfId: string) {
    try { setRuns(await listWorkflowRuns(wfId)); } catch (e) { console.error(e); }
  }

  function selectWorkflow(wf: Workflow) {
    setActiveId(wf.id);
    setYaml(wf.yaml_content);
    loadRuns(wf.id);
  }

  async function handleCreate() {
    const template = `title: "New Workflow"\ndescription: ""\nparameters: []\nblocks:\n  - block_type: navigation\n    label: step1\n    url: "https://example.com"\n`;
    setLoading(true);
    try {
      const wf = await createWorkflow(template);
      await loadWorkflows();
      selectWorkflow(wf);
    } catch (e) { console.error(e); }
    setLoading(false);
  }

  async function handleDelete(id: string) {
    if (!confirm('确定删除此工作流？')) return;
    try {
      await deleteWorkflow(id);
      if (activeId === id) { setActiveId(null); setYaml(''); }
      await loadWorkflows();
    } catch (e) { console.error(e); }
  }

  async function handleRun() {
    if (!activeId) return;
    setLoading(true);
    try {
      await runWorkflow(activeId);
      await loadRuns(activeId);
      setTab('runs');
    } catch (e) { console.error(e); }
    setLoading(false);
  }

  const activeWf = workflows.find(w => w.id === activeId);

  return (
    <div className="workflows-page">
      <div className="wf-sidebar">
        <div className="wf-sidebar-header">
          <span style={{ fontWeight: 600 }}>工作流</span>
          <button className="btn-primary" onClick={handleCreate} disabled={loading} style={{ padding: '4px 12px', fontSize: 12 }}>
            新建
          </button>
        </div>
        <div className="wf-list">
          {workflows.length === 0 ? (
            <div className="empty-state">暂无工作流</div>
          ) : workflows.map(wf => (
            <div key={wf.id} className={`task-item ${activeId === wf.id ? 'active' : ''}`} onClick={() => selectWorkflow(wf)}>
              <div className="task-item-header">
                <span style={{ fontWeight: 500, fontSize: 13 }}>{wf.title || wf.id.slice(0, 8)}</span>
                <button className="btn-ghost" onClick={(e) => { e.stopPropagation(); handleDelete(wf.id); }} style={{ marginLeft: 'auto', padding: '2px 6px', fontSize: 11 }}>删除</button>
              </div>
              {wf.description && <div className="task-item-text">{wf.description}</div>}
            </div>
          ))}
        </div>
      </div>

      <div className="wf-detail">
        {!activeWf ? (
          <div className="empty-state" style={{ height: '100%' }}>选择或新建一个工作流</div>
        ) : (
          <>
            <div className="task-detail-header">
              <span style={{ fontWeight: 600 }}>{activeWf.title}</span>
              <div className="task-detail-actions">
                <button className="btn-primary" onClick={handleRun} disabled={loading}>
                  {loading ? <span className="spinner" /> : '运行'}
                </button>
              </div>
            </div>

            <div className="tab-bar">
              <button className={`tab-btn ${tab === 'editor' ? 'active' : ''}`} onClick={() => setTab('editor')}>YAML 编辑</button>
              <button className={`tab-btn ${tab === 'runs' ? 'active' : ''}`} onClick={() => setTab('runs')}>
                运行历史 {runs.length > 0 && `(${runs.length})`}
              </button>
            </div>

            <div className="tab-content">
              {tab === 'editor' && (
                <textarea
                  className="yaml-editor"
                  value={yaml}
                  onChange={(e) => setYaml(e.target.value)}
                  spellCheck={false}
                />
              )}
              {tab === 'runs' && (
                runs.length === 0 ? <div className="empty-state">暂无运行记录</div> : (
                  <div className="run-list">
                    {runs.map(run => (
                      <div key={run.run_id} className="run-item">
                        <span className={`badge badge-${run.status}`}>{run.status}</span>
                        <span style={{ flex: 1, fontSize: 13 }}>{run.run_id.slice(0, 8)}</span>
                        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{run.started_at?.slice(0, 19)}</span>
                      </div>
                    ))}
                  </div>
                )
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
