import { useState } from 'react';
import { submitTasks } from '@/api/tasks';

export function TaskForm({ onSubmit }: { onSubmit?: () => void }) {
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit() {
    const tasks = text.split('---').map(t => t.trim()).filter(Boolean);
    if (!tasks.length) return;
    setLoading(true);
    try {
      await submitTasks(tasks);
      setText('');
      onSubmit?.();
    } catch (e) {
      console.error('提交失败:', e);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="task-form">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="输入任务描述...&#10;多个任务用 --- 分隔"
        rows={4}
        onKeyDown={(e) => { if (e.key === 'Enter' && e.ctrlKey) handleSubmit(); }}
      />
      <button className="btn-primary" onClick={handleSubmit} disabled={loading || !text.trim()}>
        {loading ? <span className="spinner" /> : '提交任务'}
      </button>
    </div>
  );
}
