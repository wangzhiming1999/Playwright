import { useState } from 'react';
import { submitTasks } from '@/api/tasks';
import { toast } from '@/utils/toast';

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
      toast.success(`已提交 ${tasks.length} 个任务`);
      onSubmit?.();
    } catch (e) {
      toast.error(`提交失败: ${e instanceof Error ? e.message : String(e)}`);
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
