import { useState, useEffect, useCallback, useRef } from 'react';
import { listWorkflows, createWorkflow, updateWorkflow, deleteWorkflow, runWorkflow, listWorkflowRuns } from '@/api/workflows';
import type { Workflow, WorkflowRun } from '@/types/workflow';
import { toast } from '@/utils/toast';
import { WorkflowEditor } from '@/components/workflows/WorkflowEditor';
import type { BlockData } from '@/components/workflows/flowUtils';
import '@/components/workflows/WorkflowEditor.css';
import './WorkflowsPage.css';

/**
 * 简易 YAML 解析：提取 blocks 数组（不引入完整 YAML 库，前端只做可视化）
 * 真正的解析在后端，这里做 best-effort 提取
 */
function parseBlocksFromYaml(yamlStr: string): BlockData[] {
  try {
    // 找到 blocks: 行，然后逐个提取 block_type + label + 其他字段
    const blocksMatch = yamlStr.match(/^blocks:\s*$/m);
    if (!blocksMatch) return [];

    const blocksStart = yamlStr.indexOf(blocksMatch[0]) + blocksMatch[0].length;
    const blocksSection = yamlStr.slice(blocksStart);

    const blocks: BlockData[] = [];
    // 匹配每个 "- block_type: xxx" 开头的块
    const blockRegex = /^  - block_type:\s*(\S+)/gm;
    let match;
    const blockStarts: { type: string; pos: number }[] = [];

    while ((match = blockRegex.exec(blocksSection)) !== null) {
      blockStarts.push({ type: match[1], pos: match.index });
    }

    for (let i = 0; i < blockStarts.length; i++) {
      const start = blockStarts[i].pos;
      const end = i + 1 < blockStarts.length ? blockStarts[i + 1].pos : blocksSection.length;
      const blockText = blocksSection.slice(start, end);

      const block: BlockData = {
        block_type: blockStarts[i].type,
        label: '',
      };

      // 提取 key: value 对（缩进 4 空格的行）
      const fieldRegex = /^    (\w+):\s*(.+)$/gm;
      let fieldMatch;
      while ((fieldMatch = fieldRegex.exec(blockText)) !== null) {
        const key = fieldMatch[1];
        let value: string | number | boolean = fieldMatch[2].trim();
        // 去掉引号
        if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
          value = value.slice(1, -1);
        }
        // 数字
        if (/^\d+(\.\d+)?$/.test(String(value))) {
          value = Number(value);
        }
        // 布尔
        if (value === 'true') value = true;
        if (value === 'false') value = false;

        (block as Record<string, unknown>)[key] = value;
      }

      if (!block.label) block.label = `step_${i + 1}`;
      blocks.push(block);
    }

    return blocks;
  } catch {
    return [];
  }
}

/**
 * 将 blocks 数组序列化回 YAML 字符串（保留原始 YAML 的 title/description/parameters 头部）
 */
function blocksToYaml(blocks: BlockData[], originalYaml: string): string {
  // 保留 blocks: 之前的头部
  const blocksIdx = originalYaml.indexOf('\nblocks:');
  const header = blocksIdx >= 0 ? originalYaml.slice(0, blocksIdx) : originalYaml.split('\nblocks:')[0] || 'title: "Workflow"\ndescription: ""\nparameters: []';

  let yaml = header + '\nblocks:\n';
  for (const block of blocks) {
    yaml += `  - block_type: ${block.block_type}\n`;
    yaml += `    label: ${block.label}\n`;
    for (const [key, value] of Object.entries(block)) {
      if (key === 'block_type' || key === 'label') continue;
      if (value === undefined || value === null || value === '') continue;
      if (typeof value === 'object') {
        yaml += `    ${key}: ${JSON.stringify(value)}\n`;
      } else if (typeof value === 'string' && (value.includes(':') || value.includes('#') || value.includes('"'))) {
        yaml += `    ${key}: "${value.replace(/"/g, '\\"')}"\n`;
      } else {
        yaml += `    ${key}: ${value}\n`;
      }
    }
  }
  return yaml;
}

export function WorkflowsPage() {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [yaml, setYaml] = useState('');
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<'visual' | 'yaml' | 'runs'>('visual');
  const [blocks, setBlocks] = useState<BlockData[]>([]);
  const [dirty, setDirty] = useState(false);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => { loadWorkflows(); }, []);

  async function loadWorkflows() {
    try { setWorkflows(await listWorkflows()); } catch { toast.error('加载工作流失败'); }
  }

  async function loadRuns(wfId: string) {
    try { setRuns(await listWorkflowRuns(wfId)); } catch { toast.error('加载运行记录失败'); }
  }

  function selectWorkflow(wf: Workflow) {
    setActiveId(wf.id);
    setYaml(wf.yaml_content);
    setBlocks(parseBlocksFromYaml(wf.yaml_content));
    setDirty(false);
    loadRuns(wf.id);
  }

  async function handleCreate() {
    const template = `title: "New Workflow"\ndescription: ""\nparameters: []\nblocks:\n  - block_type: navigation\n    label: step1\n    url: "https://example.com"\n`;
    setLoading(true);
    try {
      const wf = await createWorkflow(template);
      await loadWorkflows();
      selectWorkflow(wf);
      toast.success('工作流已创建');
    } catch { toast.error('创建失败'); }
    setLoading(false);
  }

  async function handleDelete(id: string) {
    if (!confirm('确定删除此工作流？')) return;
    try {
      await deleteWorkflow(id);
      if (activeId === id) { setActiveId(null); setYaml(''); setBlocks([]); }
      await loadWorkflows();
      toast.success('已删除');
    } catch { toast.error('删除失败'); }
  }

  async function handleRun() {
    if (!activeId) return;
    // 运行前先保存
    if (dirty) await handleSave();
    setLoading(true);
    try {
      await runWorkflow(activeId);
      await loadRuns(activeId);
      setTab('runs');
      toast.success('工作流已启动');
    } catch { toast.error('运行失败'); }
    setLoading(false);
  }

  // 保存 YAML 到后端
  async function handleSave() {
    if (!activeId) return;
    try {
      await updateWorkflow(activeId, yaml);
      setDirty(false);
      await loadWorkflows();
      toast.success('已保存');
    } catch { toast.error('保存失败'); }
  }

  // 可视化编辑器 → blocks 变更 → 同步到 YAML
  const handleBlocksChange = useCallback((newBlocks: BlockData[]) => {
    setBlocks(newBlocks);
    setYaml(prev => blocksToYaml(newBlocks, prev));
    setDirty(true);

    // 自动保存（防抖 2 秒）
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      // 触发保存（通过 ref 获取最新 activeId）
    }, 2000);
  }, []);

  // YAML 编辑 → 同步到 blocks
  function handleYamlChange(newYaml: string) {
    setYaml(newYaml);
    setBlocks(parseBlocksFromYaml(newYaml));
    setDirty(true);
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
              <div className="task-detail-actions" style={{ display: 'flex', gap: 8 }}>
                {dirty && (
                  <button className="btn-secondary" onClick={handleSave} style={{ padding: '4px 12px', fontSize: 12 }}>
                    保存
                  </button>
                )}
                <button className="btn-primary" onClick={handleRun} disabled={loading}>
                  {loading ? <span className="spinner" /> : '运行'}
                </button>
              </div>
            </div>

            <div className="tab-bar">
              <button className={`tab-btn ${tab === 'visual' ? 'active' : ''}`} onClick={() => setTab('visual')}>
                可视化编辑
              </button>
              <button className={`tab-btn ${tab === 'yaml' ? 'active' : ''}`} onClick={() => setTab('yaml')}>
                YAML
              </button>
              <button className={`tab-btn ${tab === 'runs' ? 'active' : ''}`} onClick={() => setTab('runs')}>
                运行历史 {runs.length > 0 && `(${runs.length})`}
              </button>
            </div>

            <div className="tab-content" style={{ flex: 1, overflow: 'hidden' }}>
              {tab === 'visual' && (
                <WorkflowEditor
                  blocks={blocks}
                  onChange={handleBlocksChange}
                  title={activeWf.title}
                />
              )}
              {tab === 'yaml' && (
                <textarea
                  className="yaml-editor"
                  value={yaml}
                  onChange={(e) => handleYamlChange(e.target.value)}
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
