import { useEffect, useState, useRef } from 'react';
import { useRecordingStore } from '@/hooks/useRecordingStore';
import {
  startRecording, stopRecording, convertRecording, replayRecording,
  deleteRecordingAction, updateRecordingAction, replaceRecordingActions, previewRecordingConvert,
} from '@/api/recordings';
import type { Recording, RecordedAction } from '@/api/recordings';
import './RecordingsPage.css';

const ACTION_ICONS: Record<string, string> = {
  click: '👆',
  type_text: '⌨️',
  navigate: '🔗',
  scroll: '⬇️',
  select_option: '📋',
  press_key: '⭐',
};

const STATUS_LABELS: Record<string, string> = {
  recording: '🔴 录制中',
  completed: '✅ 已完成',
  converted: '🔄 已转换',
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

interface PreviewResult {
  original_count: number;
  cleaned_count: number;
  parameters: Recording['parameters'];
  yaml_preview: string;
}

function ActionRow({
  action, index, editable, onDelete, onEdit, onDragStart, onDragOver, onDrop,
}: {
  action: RecordedAction;
  index: number;
  editable: boolean;
  onDelete: (i: number) => void;
  onEdit: (i: number, text: string) => void;
  onDragStart: (i: number) => void;
  onDragOver: (e: React.DragEvent, i: number) => void;
  onDrop: (i: number) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [editVal, setEditVal] = useState(action.text || '');

  function commitEdit() {
    setEditing(false);
    if (editVal !== action.text) onEdit(index, editVal);
  }

  return (
    <div
      className="timeline-item"
      draggable={editable}
      onDragStart={() => onDragStart(index)}
      onDragOver={e => { e.preventDefault(); onDragOver(e, index); }}
      onDrop={() => onDrop(index)}
      style={{ cursor: editable ? 'grab' : 'default' }}
    >
      {editable && <span className="drag-handle" title="拖拽排序">⠿</span>}
      <span className="timeline-step">{index + 1}</span>
      <span className="timeline-icon">{ACTION_ICONS[action.type] || '❓'}</span>
      <span className="timeline-type">{action.type}</span>
      {editing ? (
        <input
          className="timeline-edit-input"
          value={editVal}
          onChange={e => setEditVal(e.target.value)}
          onBlur={commitEdit}
          onKeyDown={e => { if (e.key === 'Enter') commitEdit(); if (e.key === 'Escape') setEditing(false); }}
          autoFocus
        />
      ) : (
        <span
          className="timeline-text"
          onClick={() => editable && action.type === 'type_text' && setEditing(true)}
          title={editable && action.type === 'type_text' ? '点击编辑' : undefined}
          style={{ cursor: editable && action.type === 'type_text' ? 'text' : 'default' }}
        >
          {action.text?.slice(0, 80) || action.selector?.slice(0, 80) || ''}
        </span>
      )}
      <span className="timeline-url">{action.url ? (() => { try { return new URL(action.url).pathname; } catch { return ''; } })() : ''}</span>
      {editable && (
        <button
          className="btn-sm btn-danger timeline-delete"
          onClick={e => { e.stopPropagation(); onDelete(index); }}
          title="删除此操作"
        >✕</button>
      )}
    </div>
  );
}

export function RecordingsPage() {
  const { recordings, loading, fetch: fetchRecordings, remove, setActive, activeRecordingId, updateRecording } = useRecordingStore();
  const [startForm, setStartForm] = useState({ title: '', start_url: '', browser_mode: 'builtin' });
  const [isStarting, setIsStarting] = useState(false);
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [yamlPreview, setYamlPreview] = useState<string | null>(null);
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const dragIndexRef = useRef<number | null>(null);

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

  const handlePreview = async (id: string) => {
    setPreviewing(true);
    try {
      const res = await previewRecordingConvert(id);
      setPreviewResult(res);
    } catch (e) {
      alert('预览失败: ' + (e as Error).message);
    } finally {
      setPreviewing(false);
    }
  };

  const handleDeleteAction = async (recordingId: string, index: number) => {
    try {
      await deleteRecordingAction(recordingId, index);
      fetchRecordings();
    } catch (e) {
      alert('删除失败: ' + (e as Error).message);
    }
  };

  const handleEditAction = async (recordingId: string, index: number, text: string) => {
    try {
      await updateRecordingAction(recordingId, index, { text });
      fetchRecordings();
    } catch (e) {
      alert('编辑失败: ' + (e as Error).message);
    }
  };

  const handleDrop = async (recording: Recording, fromIndex: number, toIndex: number) => {
    if (fromIndex === toIndex) return;
    const newActions = [...recording.actions];
    const [moved] = newActions.splice(fromIndex, 1);
    newActions.splice(toIndex, 0, moved);
    // 乐观更新
    updateRecording(recording.id, { actions: newActions });
    try {
      await replaceRecordingActions(recording.id, newActions);
    } catch (e) {
      alert('排序保存失败: ' + (e as Error).message);
      fetchRecordings(); // 回滚
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
              {isStarting ? '启动中...' : '🔴 开始录制'}
            </button>
          </div>
        ) : (
          <div className="recording-active">
            <span className="recording-indicator">🔴 录制中...</span>
            {activeRecording && <span>{activeRecording.actions.length} 步操作</span>}
            <button className="btn-stop" onClick={handleStop}>⏹ 停止录制</button>
          </div>
        )}
      </div>

      {/* 实时操作时间线（录制中） */}
      {activeSession && activeRecording && activeRecording.actions.length > 0 && (
        <div className="recording-timeline">
          <h3>操作记录</h3>
          {activeRecording.actions.map((a, i) => (
            <div key={i} className="timeline-item">
              <span className="timeline-icon">{ACTION_ICONS[a.type] || '❓'}</span>
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
              {activeRecording.status !== 'recording' && (
                <button className="btn-secondary" onClick={() => handlePreview(activeRecording.id)} disabled={previewing}>
                  {previewing ? '预览中...' : '🔍 预览转换'}
                </button>
              )}
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

          {/* 操作时间线（可编辑） */}
          <div className="recording-timeline">
            <h4>
              操作步骤 ({activeRecording.actions.length})
              {activeRecording.status !== 'recording' && (
                <span className="timeline-hint"> — 可拖拽排序，点击文字编辑，✕ 删除</span>
              )}
            </h4>
            {activeRecording.actions.map((a, i) => (
              <ActionRow
                key={`${i}-${a.timestamp}`}
                action={a}
                index={i}
                editable={activeRecording.status !== 'recording'}
                onDelete={idx => handleDeleteAction(activeRecording.id, idx)}
                onEdit={(idx, text) => handleEditAction(activeRecording.id, idx, text)}
                onDragStart={idx => { dragIndexRef.current = idx; }}
                onDragOver={(e, _idx) => e.preventDefault()}
                onDrop={toIdx => {
                  if (dragIndexRef.current !== null) {
                    handleDrop(activeRecording, dragIndexRef.current, toIdx);
                    dragIndexRef.current = null;
                  }
                }}
              />
            ))}
          </div>
        </div>
      )}

      {/* 转换预览模态框 */}
      {previewResult && (
        <div className="modal-overlay" onClick={() => setPreviewResult(null)}>
          <div className="modal-content yaml-modal" onClick={e => e.stopPropagation()}>
            <h3>转换预览</h3>
            <div className="preview-stats">
              <span>原始操作：<strong>{previewResult.original_count}</strong></span>
              <span>清洗后：<strong>{previewResult.cleaned_count}</strong></span>
              <span>减少：<strong>{previewResult.original_count - previewResult.cleaned_count}</strong> 步</span>
            </div>
            {previewResult.parameters.length > 0 && (
              <div className="preview-params">
                <h4>检测到的参数</h4>
                {previewResult.parameters.map((p, i) => (
                  <div key={i} className="param-item">
                    <span className="param-key">{p.key}</span>
                    <span className="param-type">{p.type}</span>
                    <span className="param-desc">{p.description}</span>
                  </div>
                ))}
              </div>
            )}
            <h4>YAML 预览</h4>
            <pre className="yaml-preview">{previewResult.yaml_preview}</pre>
            <div className="modal-actions">
              <button onClick={() => { navigator.clipboard.writeText(previewResult.yaml_preview); }}>复制 YAML</button>
              <button onClick={() => setPreviewResult(null)}>关闭</button>
            </div>
          </div>
        </div>
      )}

      {/* YAML 预览（转换后） */}
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
