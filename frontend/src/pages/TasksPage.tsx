import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useTaskStore } from '@/hooks/useTaskStore';
import { TaskList } from '@/components/tasks/TaskList';
import { TaskDetail } from '@/components/tasks/TaskDetail';
import { TaskForm } from '@/components/tasks/TaskForm';
import './TasksPage.css';

export function TasksPage() {
  const { taskId } = useParams();
  const activeTaskId = useTaskStore((s) => s.activeTaskId);
  const selectTask = useTaskStore((s) => s.selectTask);
  const [showForm, setShowForm] = useState(true);

  // Sync URL param with store
  if (taskId && taskId !== activeTaskId) {
    selectTask(taskId);
  }

  return (
    <div className="tasks-page">
      <div className="tasks-sidebar">
        {showForm && <TaskForm onSubmit={() => setShowForm(false)} />}
        <button className="btn-ghost" onClick={() => setShowForm(!showForm)} style={{ margin: '8px 12px' }}>
          {showForm ? '隐藏表单' : '显示表单'}
        </button>
        <TaskList />
      </div>
      <div className="tasks-detail">
        <TaskDetail />
      </div>
    </div>
  );
}
