import { useEffect, useState } from 'react';
import { cleanup, getPool, resizePool } from '@/api/tasks';
import { toast } from '@/utils/toast';
import './SettingsPage.css';

export function SettingsPage() {
  const [apiKey, setApiKey] = useState(localStorage.getItem('api_key') || '');
  const [keepLast, setKeepLast] = useState(20);
  const [cleanupResult, setCleanupResult] = useState('');
  const [saving, setSaving] = useState(false);

  // Pool 配置
  const [poolWorkers, setPoolWorkers] = useState(3);
  const [poolRunning, setPoolRunning] = useState(0);
  const [poolQueued, setPoolQueued] = useState(0);
  const [poolLoading, setPoolLoading] = useState(false);

  // 浏览器模式
  const [browserMode, setBrowserMode] = useState(localStorage.getItem('browser_mode') || 'builtin');

  useEffect(() => {
    getPool().then((p) => {
      setPoolWorkers(p.max_workers);
      setPoolRunning(p.running);
      setPoolQueued(p.queued);
    }).catch(() => {});
  }, []);

  function handleSaveApiKey() {
    localStorage.setItem('api_key', apiKey);
    toast.success('API Key 已保存');
    setSaving(true);
    setTimeout(() => setSaving(false), 1000);
  }

  function handleBrowserMode(mode: string) {
    setBrowserMode(mode);
    localStorage.setItem('browser_mode', mode);
    toast.success(`浏览器模式已切换为 ${mode}`);
  }

  async function handleResizePool() {
    setPoolLoading(true);
    try {
      const res = await resizePool(poolWorkers);
      toast.success(`并发数已调整: ${res.old_max_workers} → ${res.new_max_workers}`);
    } catch (e) {
      toast.error(`调整失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setPoolLoading(false);
    }
  }

  async function handleCleanup() {
    try {
      const result = await cleanup(keepLast);
      setCleanupResult(`已清理 ${result.deleted_tasks} 个任务`);
      toast.success(`已清理 ${result.deleted_tasks} 个任务`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setCleanupResult(`清理失败: ${msg}`);
      toast.error(`清理失败: ${msg}`);
    }
  }

  return (
    <div className="settings-page">
      <h1>设置</h1>

      <section className="settings-section">
        <h2>API 认证</h2>
        <p className="settings-desc">设置 API Key 后，所有请求会自动带上 X-API-Key 头</p>
        <div className="settings-row">
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="输入 API Key..."
            style={{ maxWidth: 400 }}
          />
          <button className="btn-primary" onClick={handleSaveApiKey}>
            {saving ? '已保存 ✓' : '保存'}
          </button>
        </div>
      </section>

      <section className="settings-section">
        <h2>并发任务池</h2>
        <p className="settings-desc">控制同时运行的浏览器实例数量（当前: {poolRunning} 运行中, {poolQueued} 排队中）</p>
        <div className="settings-row">
          <label style={{ fontSize: 13, color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>最大并发数</label>
          <input
            type="range"
            min={1}
            max={10}
            value={poolWorkers}
            onChange={(e) => setPoolWorkers(Number(e.target.value))}
            style={{ width: 160 }}
          />
          <span style={{ fontSize: 14, fontWeight: 600, minWidth: 24, textAlign: 'center' }}>{poolWorkers}</span>
          <button className="btn-primary" onClick={handleResizePool} disabled={poolLoading}>
            {poolLoading ? '调整中...' : '应用'}
          </button>
        </div>
      </section>

      <section className="settings-section">
        <h2>浏览器模式</h2>
        <p className="settings-desc">选择任务执行时使用的浏览器模式</p>
        <div className="settings-row" style={{ gap: 16 }}>
          {[
            { value: 'builtin', label: '内置 Chromium', desc: '开箱即用，独立实例' },
            { value: 'user_chrome', label: '用户 Chrome', desc: '复用登录态和配置' },
            { value: 'cdp', label: 'CDP 远程', desc: '连接已打开的浏览器' },
          ].map((opt) => (
            <label key={opt.value} className={`browser-mode-option ${browserMode === opt.value ? 'active' : ''}`}>
              <input
                type="radio"
                name="browserMode"
                value={opt.value}
                checked={browserMode === opt.value}
                onChange={() => handleBrowserMode(opt.value)}
              />
              <span className="browser-mode-label">{opt.label}</span>
              <span className="browser-mode-desc">{opt.desc}</span>
            </label>
          ))}
        </div>
      </section>

      <section className="settings-section">
        <h2>数据清理</h2>
        <p className="settings-desc">清理旧的已完成任务，释放存储空间</p>
        <div className="settings-row">
          <label style={{ fontSize: 13, color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>保留最近</label>
          <input
            type="number"
            value={keepLast}
            onChange={(e) => setKeepLast(Number(e.target.value))}
            min={1}
            max={100}
            style={{ width: 80 }}
          />
          <span style={{ fontSize: 13, color: 'var(--text-secondary)' }}>个任务</span>
          <button className="btn-danger" onClick={handleCleanup}>清理</button>
        </div>
        {cleanupResult && <p style={{ fontSize: 13, color: 'var(--green)', marginTop: 8 }}>{cleanupResult}</p>}
      </section>
    </div>
  );
}
