import { Outlet, Link, useLocation } from 'react-router-dom';
import { useThemeStore } from '@/hooks/useThemeStore';
import './AppShell.css';

export function AppShell() {
  const location = useLocation();
  const theme = useThemeStore((s) => s.theme);
  const toggleTheme = useThemeStore((s) => s.toggle);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="logo">Skyvern</div>
        <nav className="nav">
          <Link to="/dashboard" className={location.pathname === '/dashboard' ? 'active' : ''}>
            Dashboard
          </Link>
          <Link to="/tasks" className={location.pathname.startsWith('/tasks') ? 'active' : ''}>
            任务
          </Link>
          <Link to="/explore" className={location.pathname.startsWith('/explore') ? 'active' : ''}>
            网站探索
          </Link>
          <Link to="/workflows" className={location.pathname.startsWith('/workflows') ? 'active' : ''}>
            工作流
          </Link>
          <Link to="/settings" className={location.pathname === '/settings' ? 'active' : ''}>
            设置
          </Link>
        </nav>
        <div className="sidebar-footer">
          <button className="theme-toggle" onClick={toggleTheme} title={theme === 'dark' ? '切换亮色主题' : '切换暗色主题'}>
            {theme === 'dark' ? '\u2600\uFE0F' : '\uD83C\uDF19'} {theme === 'dark' ? '亮色模式' : '暗色模式'}
          </button>
        </div>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
