import { useState, useRef, useEffect } from 'react';
import { useTaskStore } from '@/hooks/useTaskStore';
import { cancelTask, deleteTask, replyToTask } from '@/api/tasks';
import './TaskDetail.css';

function classifyLog(line: string) {
  if (line.includes('>>>') || line.includes('[') && line.includes(']')) return 'log-action';
  if (line.includes('✓') || line.includes('✅') || line.includes('成功')) return 'log-ok';
  if (line.includes('⚠') || line.includes('WARNING')) return 'log-warn';
  if (line.includes('❌') || line.includes('失败') || line.includes('ERROR')) return 'log-err';
  if (line.includes('__PROGRESS__')) return 'log-progress';
  return '';
}

export function TaskDetail() {
  const activeTaskId = useTaskStore((s) => s.activeTaskId);
  const task = useTaskStore((s) => activeTaskId ? s.tasks[activeTaskId] : null);
  const removeTask = useTaskStore((s) => s.removeTask);
  const [tab, setTab] = useState<'logs' | 'screenshots'>('logs');
  const [lightbox, setLightbox] = useState<string | null>(null);
  const [reply, setReply] = useState('');
  const [replying, setReplying] = useState(false);
  const logEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [task?.logs.length]);

  if (!task) {
    return <div className="empty-state" style={{ height: '100%' }}>选择一个任务查看详情</div>;
  }

  async function handleCancel() {
    try { await cancelTask(task!.id); } catch (e) { console.error(e); }
  }

  async function handleDelete() {
    if (!confirm('确定删除此任务？')) return;
    try {
      await deleteTask(task!.id);
      removeTask(task!.id);
    } catch (e) { console.error(e); }
  }

  async function handleReply() {
    if (!reply.trim()) return;
    setReplying(true);
    try {
      await replyToTask(task!.id, reply);
      setReply('');
    } catch (e) { console.error(e); }
    setReplying(false);
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
        <button className={`tab-btn ${tab === 'logs' ? 'active' : ''}`} onClick={() => setTab('logs')}>
          日志
        </button>
        <button className={`tab-btn ${tab === 'screenshots' ? 'active' : ''}`} onClick={() => setTab('screenshots')}>
          截图 {screenshotCount > 0 && `(${screenshotCount})`}
        </button>
      </div>

      <div className="tab-content">
        {tab === 'logs' && (
          <div className="log-viewer">
            {task.logs.filter(l => !l.startsWith('__PROGRESS__')).map((line, i) => (
              <div key={i} className={`log-line ${classifyLog(line)}`}>{line}</div>
            ))}
            <div ref={logEndRef} />
          </div>
        )}

        {tab === 'screenshots' && (
          <>
            {screenshotCount === 0 ? (
              <div className="empty-state">暂无截图</div>
            ) : (
              <div className="screenshot-grid">
                {task.screenshots.map((filename) => (
                  <div key={filename} className="screenshot-card" onClick={() => setLightbox(filename)}>
                    <img src={`/screenshots/${task.id}/${filename}`} alt={filename} loading="lazy" />
                    <div className="screenshot-card-name">{filename}</div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {lightbox && (
        <div className="lightbox-overlay" onClick={() => setLightbox(null)}>
          <button className="lightbox-close" onClick={() => setLightbox(null)}>×</button>
          <img
            src={`/screenshots/${task.id}/${lightbox}`}
            alt={lightbox}
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </div>
  );
}
