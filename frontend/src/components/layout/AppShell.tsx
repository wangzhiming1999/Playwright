import { Outlet, Link, useLocation } from 'react-router-dom';
import './AppShell.css';

export function AppShell() {
  const location = useLocation();

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
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
