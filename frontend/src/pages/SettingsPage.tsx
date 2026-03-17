import { useState } from 'react';
import { cleanup } from '@/api/tasks';
import './SettingsPage.css';

export function SettingsPage() {
  const [apiKey, setApiKey] = useState(localStorage.getItem('api_key') || '');
  const [keepLast, setKeepLast] = useState(20);
  const [cleanupResult, setCleanupResult] = useState('');
  const [saving, setSaving] = useState(false);

  function handleSaveApiKey() {
    localStorage.setItem('api_key', apiKey);
    setSaving(true);
    setTimeout(() => setSaving(false), 1000);
  }

  async function handleCleanup() {
    try {
      const result = await cleanup(keepLast);
      setCleanupResult(`已清理 ${result.deleted_tasks} 个任务`);
    } catch (e) {
      setCleanupResult(`清理失败: ${e}`);
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
