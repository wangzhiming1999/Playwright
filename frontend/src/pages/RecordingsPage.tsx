import { useEffect, useState } from 'react';
import { useRecordingStore } from '@/hooks/useRecordingStore';
import { startRecording, stopRecording, convertRecording, replayRecording } from '@/api/recordings';
import type { Recording } from '@/api/recordings';
import './RecordingsPage.css';

const ACTION_ICONS: Record<string, string> = {
  click: '\uD83D\uDC46',
  type_text: '\u2328\uFE0F',
  navigate: '\uD83D\uDD17',
  scroll: '\u2B07\uFE0F',
  select_option: '\uD83D\uDCCB',
  press_key: '\u2B50',
};

const STATUS_LABELS: Record<string, string> = {
  recording: '\uD83D\uDD34 录制中',
  completed: '\u2705 已完成',
  converted: '\uD83D\uDD04 已转换',
};

function RecordingCard({ recording, onSelect, onDelete }: {
  recording: Recording; onSelect: () => void; onDelete: () => void;
}) {
  return (
    <div className="recording-card" onClick={onSelect}>
      <div className="recording-card-header">
        <span className="recording-status">{STATUS_LABELS[recording.status] || recording.status}</span>
        <span className="recording-date">{recording.created_at?.slice(0, 16)}</span>
      </div>
      <div className="recording-card-title">{recording.title || '未命名录制'}</div>
      <div className="recording-card-meta">
        <span>{recording.actions.length} 步操作</span>
        {recording.start_url && <span className="recording-url">{recording.start_url}</span>}
      </div>
      <div className="recording-card-actions" onClick={e => e.stopPropagation()}>
        <button className="btn-sm btn-danger" onClick={onDelete}>删除</button>
      </div>
    </div>
  );
}

// PLACEHOLDER_DETAIL

export function RecordingsPage() {
  const { recordings, loading, fetch: fetchRecordings, remove, setActive, activeRecordingId } = useRecordingStore();
  const [startForm, setStartForm] = useState({ title: '', start_url: '', browser_mode: 'builtin' });
  const [isStarting, setIsStarting] = useState(false);
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [yamlPreview, setYamlPreview] = useState<string | null>(null);

  useEffect(() => { fetchRecordings(); }, []);

  const activeRecording = recordings.find(r => r.id === (activeRecordingId || activeSession));

  const handleStart = async () => {
    setIsStarting(true);
    try {
      const res = await startRecording(startForm);
      setActiveSession(res.recording_id);
      fetchRecordings();
    } catch (e) {
      alert('启动录制失败: ' + (e as Error).message);
    } finally {
      setIsStarting(false);
    }
  };

  const handleStop = async () => {
    if (!activeSession) return;
    try {
      await stopRecording(activeSession);
      setActiveSession(null);
      fetchRecordings();
    } catch (e) {
      alert('停止录制失败: ' + (e as Error).message);
    }
  };

  const handleConvert = async (id: string) => {
    try {
      const res = await convertRecording(id);
      setYamlPreview(res.yaml_content);
      fetchRecordings();
    } catch (e) {
      alert('转换失败: ' + (e as Error).message);
    }
  };

  const handleReplay = async (id: string) => {
    try {
      const res = await replayRecording(id);
      alert(`回放已启动，workflow_id: ${res.workflow_id}`);
    } catch (e) {
      alert('回放失败: ' + (e as Error).message);
    }
  };

  return (
    <div className="recordings-page">
      <div className="recordings-header">
        <h2>录制回放</h2>
        <p className="recordings-desc">录制浏览器操作，自动生成可复用的工作流</p>
      </div>

      {/* 录制控制面板 */}
      <div className="recording-control-panel">
        {!activeSession ? (
          <div className="recording-start-form">
            <input
              type="text" placeholder="录制标题（可选）"
              value={startForm.title} onChange={e => setStartForm(s => ({ ...s, title: e.target.value }))}
            />
            <input
              type="text" placeholder="起始 URL（可选）"
              value={startForm.start_url} onChange={e => setStartForm(s => ({ ...s, start_url: e.target.value }))}
            />
            <select value={startForm.browser_mode} onChange={e => setStartForm(s => ({ ...s, browser_mode: e.target.value }))}>
              <option value="builtin">内置浏览器</option>
              <option value="cdp">CDP 远程</option>
            </select>
            <button className="btn-primary" onClick={handleStart} disabled={isStarting}>
              {isStarting ? '启动中...' : '\uD83D\uDD34 开始录制'}
            </button>
          </div>
        ) : (
          <div className="recording-active">
            <span className="recording-indicator">\uD83D\uDD34 录制中...</span>
            {activeRecording && <span>{activeRecording.actions.length} 步操作</span>}
            <button className="btn-stop" onClick={handleStop}>\u23F9 停止录制</button>
          </div>
        )}
      </div>

      {/* 实时操作时间线（录制中） */}
      {activeSession && activeRecording && activeRecording.actions.length > 0 && (
        <div className="recording-timeline">
          <h3>操作记录</h3>
          {activeRecording.actions.map((a, i) => (
            <div key={i} className="timeline-item">
              <span className="timeline-icon">{ACTION_ICONS[a.type] || '\u2753'}</span>
              <span className="timeline-type">{a.type}</span>
              <span className="timeline-text">{a.text?.slice(0, 60) || a.selector?.slice(0, 60) || ''}</span>
            </div>
          ))}
        </div>
      )}

      {/* 录制详情（选中时） */}
      {!activeSession && activeRecording && (
        <div className="recording-detail">
          <div className="recording-detail-header">
            <h3>{activeRecording.title || '未命名录制'}</h3>
            <div className="recording-detail-actions">
              {activeRecording.status === 'completed' && (
                <button className="btn-primary" onClick={() => handleConvert(activeRecording.id)}>转为工作流</button>
              )}
              {(activeRecording.status === 'completed' || activeRecording.status === 'converted') && (
                <button className="btn-secondary" onClick={() => handleReplay(activeRecording.id)}>回放</button>
              )}
              <button className="btn-sm" onClick={() => setActive(null)}>关闭</button>
            </div>
          </div>

          {/* 参数检测 */}
          {activeRecording.parameters.length > 0 && (
            <div className="recording-params">
              <h4>检测到的参数</h4>
              {activeRecording.parameters.map((p, i) => (
                <div key={i} className="param-item">
                  <span className="param-key">{p.key}</span>
                  <span className="param-type">{p.type}</span>
                  <span className="param-desc">{p.description}</span>
                  {p.default_value && <span className="param-default">默认: {p.default_value}</span>}
                </div>
              ))}
            </div>
          )}

          {/* 操作时间线 */}
          <div className="recording-timeline">
            <h4>操作步骤 ({activeRecording.actions.length})</h4>
            {activeRecording.actions.map((a, i) => (
              <div key={i} className="timeline-item">
                <span className="timeline-step">{i + 1}</span>
                <span className="timeline-icon">{ACTION_ICONS[a.type] || '\u2753'}</span>
                <span className="timeline-type">{a.type}</span>
                <span className="timeline-text">{a.text?.slice(0, 80) || a.selector?.slice(0, 80) || ''}</span>
                <span className="timeline-url">{a.url ? new URL(a.url).pathname : ''}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* YAML 预览 */}
      {yamlPreview && (
        <div className="modal-overlay" onClick={() => setYamlPreview(null)}>
          <div className="modal-content yaml-modal" onClick={e => e.stopPropagation()}>
            <h3>生成的工作流 YAML</h3>
            <pre className="yaml-preview">{yamlPreview}</pre>
            <div className="modal-actions">
              <button onClick={() => { navigator.clipboard.writeText(yamlPreview); }}>复制</button>
              <button onClick={() => setYamlPreview(null)}>关闭</button>
            </div>
          </div>
        </div>
      )}

      {/* 录制列表 */}
      {!activeSession && !activeRecordingId && (
        <>
          <h3 className="recordings-list-title">历史录制 ({recordings.length})</h3>
          {loading ? (
            <div className="recordings-loading">加载中...</div>
          ) : recordings.length === 0 ? (
            <div className="recordings-empty">暂无录制。点击上方「开始录制」按钮开始。</div>
          ) : (
            <div className="recordings-grid">
              {recordings.map(r => (
                <RecordingCard
                  key={r.id} recording={r}
                  onSelect={() => setActive(r.id)}
                  onDelete={() => remove(r.id)}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
