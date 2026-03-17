import { useState } from 'react';
import { useTaskStore } from '@/hooks/useTaskStore';
import { cancelTask, deleteTask, replyToTask, runCuration, runGenerate } from '@/api/tasks';
import { toast } from '@/utils/toast';
import { normalizeCurationResult, normalizeGenerated } from '@/utils/normalize';
import { CurationView } from '@/components/CurationView';
import { GeneratedView } from '@/components/GeneratedView';
import { LogViewer } from '@/components/LogViewer';
import { ScreenshotReplay } from '@/components/ScreenshotReplay';
import './TaskDetail.css';

export function TaskDetail() {
  const activeTaskId = useTaskStore((s) => s.activeTaskId);
  const task = useTaskStore((s) => activeTaskId ? s.tasks[activeTaskId] : null);
  const removeTask = useTaskStore((s) => s.removeTask);
  const setCuration = useTaskStore((s) => s.setCuration);
  const setGenerated = useTaskStore((s) => s.setGenerated);
  const [tab, setTab] = useState<'logs' | 'screenshots' | 'curation' | 'generated'>('logs');
  const [reply, setReply] = useState('');
  const [replying, setReplying] = useState(false);
  const [curating, setCurating] = useState(false);
  const [generating, setGenerating] = useState(false);

  if (!task) {
    return <div className="empty-state" style={{ height: '100%' }}>选择一个任务查看详情</div>;
  }

  async function handleCancel() {
    try { await cancelTask(task!.id); toast.success('已取消'); } catch (e) { toast.error(e instanceof Error ? e.message : String(e)); }
  }

  async function handleDelete() {
    if (!confirm('确定删除此任务？')) return;
    try { await deleteTask(task!.id); removeTask(task!.id); toast.success('已删除'); } catch (e) { toast.error(e instanceof Error ? e.message : String(e)); }
  }

  async function handleReply() {
    if (!reply.trim()) return;
    setReplying(true);
    try { await replyToTask(task!.id, reply); setReply(''); } catch (e) { toast.error(e instanceof Error ? e.message : String(e)); }
    setReplying(false);
  }

  async function handleCurate() {
    setCurating(true);
    try {
      const res = await runCuration(task!.id);
      setCuration(task!.id, normalizeCurationResult(res));
      toast.success('策展完成');
    } catch (e) { toast.error(`策展失败: ${e instanceof Error ? e.message : String(e)}`); }
    setCurating(false);
  }

  async function handleGenerate() {
    setGenerating(true);
    try {
      const res = await runGenerate('task', task!.id);
      setGenerated(task!.id, normalizeGenerated(res));
      toast.success('内容生成完成');
    } catch (e) { toast.error(`生成失败: ${e instanceof Error ? e.message : String(e)}`); }
    setGenerating(false);
  }

  const screenshotCount = task.screenshots.length;

  return (
    <div className="task-detail-container">
      <div className="task-detail-header">
        <span className={`badge badge-${task.status}`}>{task.status}</span>
        <span className="task-detail-title">{task.task}</span>
        <div className="task-detail-actions">
          {task.status === 'running' && (
            <button className="btn-ghost" onClick={handleCancel}>取消</button>
          )}
          <button className="btn-danger" onClick={handleDelete} style={{ padding: '6px 12px', fontSize: 12 }}>删除</button>
        </div>
      </div>

      {task.pending_question && (
        <div style={{ padding: '0 20px', paddingTop: 16 }}>
          <div className="ask-user-box">
            <div className="ask-user-label">Agent 需要你的输入</div>
            <div className="ask-user-question">{task.pending_question.question}</div>
            {task.pending_question.reason && (
              <div className="ask-user-reason">{task.pending_question.reason}</div>
            )}
            <div className="ask-user-form">
              <input
                value={reply}
                onChange={(e) => setReply(e.target.value)}
                placeholder="输入你的回答..."
                onKeyDown={(e) => { if (e.key === 'Enter') handleReply(); }}
              />
              <button onClick={handleReply} disabled={replying}>
                {replying ? '提交中...' : '提交'}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="tab-bar">
        <button className={`tab-btn ${tab === 'logs' ? 'active' : ''}`} onClick={() => setTab('logs')}>日志</button>
        <button className={`tab-btn ${tab === 'screenshots' ? 'active' : ''}`} onClick={() => setTab('screenshots')}>
          截图 {screenshotCount > 0 && `(${screenshotCount})`}
        </button>
        <button className={`tab-btn ${tab === 'curation' ? 'active' : ''}`} onClick={() => setTab('curation')}>
          策展 {task.curation && `(${task.curation.cards.length})`}
        </button>
        <button className={`tab-btn ${tab === 'generated' ? 'active' : ''}`} onClick={() => setTab('generated')}>生成内容</button>
      </div>

      <div className="tab-content">
        {tab === 'logs' && (
          <LogViewer logs={task.logs} isLive={task.status === 'running'} />
        )}

        {tab === 'screenshots' && (
          <ScreenshotReplay
            screenshots={task.screenshots}
            screenshotPrefix={`/screenshots/${task.id}`}
            logs={task.logs}
          />
        )}

        {tab === 'curation' && (
          task.curation ? (
            <CurationView curation={task.curation} screenshotPrefix={`/screenshots/${task.id}`} />
          ) : task.status === 'done' ? (
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
          task.generated ? (
            <GeneratedView generated={task.generated} source="task" sourceId={task.id} />
          ) : task.curation ? (
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
    </div>
  );
}
