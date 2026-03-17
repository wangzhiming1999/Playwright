import { useEffect, useState } from 'react';
import { cleanup, getPool, resizePool, getBrowserPool, resizeBrowserPool, warmupBrowserPool } from '@/api/tasks';
import type { BrowserPoolStats } from '@/api/tasks';
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

  // 浏览器池
  const [bpStats, setBpStats] = useState<BrowserPoolStats>({ enabled: false });
  const [bpSize, setBpSize] = useState(3);
  const [bpLoading, setBpLoading] = useState(false);
  const [bpWarming, setBpWarming] = useState(false);

  useEffect(() => {
    getPool().then((p) => {
      setPoolWorkers(p.max_workers);
      setPoolRunning(p.running);
      setPoolQueued(p.queued);
    }).catch(() => {});
    getBrowserPool().then((bp) => {
      setBpStats(bp);
      if (bp.max_size) setBpSize(bp.max_size);
    }).catch(() => {});
    // 5秒轮询浏览器池状态
    const timer = setInterval(() => {
      getBrowserPool().then(setBpStats).catch(() => {});
    }, 5000);
    return () => clearInterval(timer);
  }, []);

  function handleSaveApiKey() {
    localStorage.setItem('api_key', apiKey);
    toast.success('API Key 已保存');
    setSaving(true);
    setTimeout(() => setSaving(false), 1000);
  }

  const browserModeLabels: Record<string, string> = {
    builtin: '内置 Chromium',
    user_chrome: '用户 Chrome',
    cdp: 'CDP 远程',
  };

  function handleBrowserMode(mode: string) {
    setBrowserMode(mode);
    localStorage.setItem('browser_mode', mode);
    toast.success(`浏览器模式已切换为 ${browserModeLabels[mode] ?? mode}`);
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

  async function handleResizeBrowserPool() {
    setBpLoading(true);
    try {
      const res = await resizeBrowserPool(bpSize);
      toast.success(`浏览器池已调整: ${res.old_max_size} → ${res.new_max_size}`);
      setBpStats(res);
    } catch (e) {
      toast.error(`调整失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBpLoading(false);
    }
  }

  async function handleWarmup() {
    setBpWarming(true);
    try {
      const res = await warmupBrowserPool();
      toast.success('浏览器池预热完成');
      setBpStats(res);
    } catch (e) {
      toast.error(`预热失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBpWarming(false);
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
        <h2>浏览器池</h2>
        {!bpStats.enabled ? (
          <p className="settings-desc">浏览器池未启用（设置环境变量 USE_BROWSER_POOL=true 启用）</p>
        ) : (
          <>
            <p className="settings-desc">
              池化复用浏览器实例，避免每次任务冷启动（{bpStats.in_use ?? 0} 使用中 / {bpStats.idle ?? 0} 空闲 / {bpStats.total ?? 0} 总计）
            </p>
            <div className="settings-row">
              <label style={{ fontSize: 13, color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>池大小</label>
              <input
                type="range"
                min={1}
                max={10}
                value={bpSize}
                onChange={(e) => setBpSize(Number(e.target.value))}
                style={{ width: 160 }}
              />
              <span style={{ fontSize: 14, fontWeight: 600, minWidth: 24, textAlign: 'center' }}>{bpSize}</span>
              <button className="btn-primary" onClick={handleResizeBrowserPool} disabled={bpLoading}>
                {bpLoading ? '调整中...' : '应用'}
              </button>
              <button className="btn-secondary" onClick={handleWarmup} disabled={bpWarming} style={{ marginLeft: 8 }}>
                {bpWarming ? '预热中...' : '预热'}
              </button>
            </div>
            {bpStats.slots && bpStats.slots.length > 0 && (
              <div className="bp-slots" style={{ marginTop: 12 }}>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  {bpStats.slots.map((slot) => (
                    <div
                      key={slot.index}
                      className={`bp-slot ${slot.in_use ? 'bp-slot--active' : 'bp-slot--idle'}`}
                      title={slot.in_use ? `任务: ${slot.task_id}` : `空闲 ${slot.idle_seconds}s`}
                    >
                      <span className="bp-slot__icon">{slot.in_use ? '🔵' : slot.connected ? '🟢' : '🔴'}</span>
                      <span className="bp-slot__label">#{slot.index + 1}</span>
                      <span className="bp-slot__status">
                        {slot.in_use ? '运行中' : slot.connected ? `空闲 ${Math.round(slot.idle_seconds)}s` : '断开'}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </section>

      <section className="settings-section">
        <h2>浏览器模式</h2>
        <p className="settings-desc">选择任务执行时使用的浏览器模式</p>
        <div className="settings-row">
          <select
            className="browser-mode-select"
            value={browserMode}
            onChange={(e) => handleBrowserMode(e.target.value)}
            aria-label="浏览器模式"
          >
            <option value="builtin">内置 Chromium — 开箱即用，独立实例</option>
            <option value="user_chrome">用户 Chrome — 复用登录态和配置（需先关闭 Chrome）</option>
            <option value="cdp">CDP 远程 — 连接已打开的浏览器（如 Lightpanda）</option>
          </select>
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
