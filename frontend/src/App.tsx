import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { useSSE } from './hooks/useSSE';
import { useKeyboardShortcuts } from './hooks/useKeyboardShortcuts';
import { AppShell } from './components/layout/AppShell';
import { ErrorBoundary } from './components/ErrorBoundary';
import { ToastContainer } from './components/Toast';
import { TasksPage } from './pages/TasksPage';
import { DashboardPage } from './pages/DashboardPage';
import { ExplorePage } from './pages/ExplorePage';
import { WorkflowsPage } from './pages/WorkflowsPage';
import { TemplatesPage } from './pages/TemplatesPage';
import { SettingsPage } from './pages/SettingsPage';
import { MemoryPage } from './pages/MemoryPage';
import { RecordingsPage } from './pages/RecordingsPage';
import './styles/globals.css';

export default function App() {
  useSSE();
  useKeyboardShortcuts();

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<AppShell />}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard" element={<ErrorBoundary><DashboardPage /></ErrorBoundary>} />
          <Route path="tasks" element={<ErrorBoundary><TasksPage /></ErrorBoundary>} />
          <Route path="tasks/:taskId" element={<ErrorBoundary><TasksPage /></ErrorBoundary>} />
          <Route path="explore" element={<ErrorBoundary><ExplorePage /></ErrorBoundary>} />
          <Route path="explore/:eid" element={<ErrorBoundary><ExplorePage /></ErrorBoundary>} />
          <Route path="workflows" element={<ErrorBoundary><WorkflowsPage /></ErrorBoundary>} />
          <Route path="workflows/:id" element={<ErrorBoundary><WorkflowsPage /></ErrorBoundary>} />
          <Route path="templates" element={<ErrorBoundary><TemplatesPage /></ErrorBoundary>} />
          <Route path="templates/:templateId" element={<ErrorBoundary><TemplatesPage /></ErrorBoundary>} />
          <Route path="memory" element={<ErrorBoundary><MemoryPage /></ErrorBoundary>} />
          <Route path="recordings" element={<ErrorBoundary><RecordingsPage /></ErrorBoundary>} />
          <Route path="settings" element={<ErrorBoundary><SettingsPage /></ErrorBoundary>} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Route>
      </Routes>
      <ToastContainer />
    </BrowserRouter>
  );
}
