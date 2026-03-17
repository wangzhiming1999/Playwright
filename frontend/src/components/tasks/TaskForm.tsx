import { useState } from 'react';
import { submitTasks } from '@/api/tasks';
import { toast } from '@/utils/toast';

export function TaskForm({ onSubmit }: { onSubmit?: () => void }) {
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(false);
  const [browserMode, setBrowserMode] = useState(() => localStorage.getItem('browser_mode') || 'builtin');

  async function handleSubmit() {
    const tasks = text.split('---').map(t => t.trim()).filter(Boolean);
    if (!tasks.length) return;
    setLoading(true);
    try {
      const cdpUrl = localStorage.getItem('cdp_url') || undefined;
      const chromeProfile = localStorage.getItem('chrome_profile') || undefined;
      await submitTasks(tasks, browserMode, cdpUrl, chromeProfile);
      setText('');
      toast.success(`已提交 ${tasks.length} 个任务（${browserMode === 'cdp' ? 'CDP 远程' : browserMode === 'user_chrome' ? '用户 Chrome' : '内置 Chromium'}）`);
      onSubmit?.();
    } catch (e) {
      toast.error(`提交失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  }

  function handleBrowserModeChange(mode: string) {
    setBrowserMode(mode);
    localStorage.setItem('browser_mode', mode);
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
      <div className="task-form-actions">
        <select
          className="task-form-browser-mode"
          value={browserMode}
          onChange={(e) => handleBrowserModeChange(e.target.value)}
          aria-label="浏览器模式"
          title="选择本次任务使用的浏览器模式"
        >
          <option value="builtin">内置 Chromium</option>
          <option value="user_chrome">用户 Chrome</option>
          <option value="cdp">CDP 远程</option>
        </select>
        <button className="btn-primary" onClick={handleSubmit} disabled={loading || !text.trim()}>
          {loading ? <span className="spinner" /> : '提交任务'}
        </button>
      </div>
    </div>
  );
}
