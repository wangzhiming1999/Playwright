import { create } from 'zustand';
import type { Task, TaskStatus } from '@/types/task';

interface TaskStore {
  tasks: Record<string, Task>;
  activeTaskId: string | null;
  searchQuery: string;
  statusFilter: TaskStatus | 'all';

  setSnapshot: (tasks: Task[]) => void;
  addTask: (task: Task) => void;
  updateStatus: (taskId: string, status: TaskStatus, screenshots?: string[]) => void;
  appendLog: (taskId: string, message: string) => void;
  updateProgress: (taskId: string, current: number, total: number) => void;
  addScreenshot: (taskId: string, filename: string) => void;
  setWaitingInput: (taskId: string, question: string, reason: string) => void;
  removeTask: (taskId: string) => void;
  selectTask: (taskId: string | null) => void;
  setSearch: (query: string) => void;
  setFilter: (status: TaskStatus | 'all') => void;
  setCuration: (taskId: string, curation: Task['curation']) => void;
  setGenerated: (taskId: string, generated: Task['generated']) => void;
}

export const useTaskStore = create<TaskStore>((set) => ({
  tasks: {},
  activeTaskId: null,
  searchQuery: '',
  statusFilter: 'all',

  setSnapshot: (tasks) => set({
    tasks: Object.fromEntries(tasks.map(t => [t.id, { ...t, logs: t.logs || [], screenshots: t.screenshots || [] }])),
  }),

  addTask: (task) => set((s) => ({
    tasks: { ...s.tasks, [task.id]: { ...task, logs: task.logs || [], screenshots: task.screenshots || [] } },
  })),

  updateStatus: (taskId, status, screenshots) => set((s) => {
    const t = s.tasks[taskId];
    if (!t) return s;
    return { tasks: { ...s.tasks, [taskId]: { ...t, status, screenshots: screenshots ?? t.screenshots, pending_question: undefined } } };
  }),

  appendLog: (taskId, message) => set((s) => {
    const t = s.tasks[taskId];
    if (!t) return s;
    return { tasks: { ...s.tasks, [taskId]: { ...t, logs: [...t.logs, message] } } };
  }),

  updateProgress: (taskId, current, total) => set((s) => {
    const t = s.tasks[taskId];
    if (!t) return s;
    return { tasks: { ...s.tasks, [taskId]: { ...t, progress: { current, total } } } };
  }),

  addScreenshot: (taskId, filename) => set((s) => {
    const t = s.tasks[taskId];
    if (!t) return s;
    if (t.screenshots.includes(filename)) return s;
    return { tasks: { ...s.tasks, [taskId]: { ...t, screenshots: [...t.screenshots, filename] } } };
  }),

  setWaitingInput: (taskId, question, reason) => set((s) => {
    const t = s.tasks[taskId];
    if (!t) return s;
    return { tasks: { ...s.tasks, [taskId]: { ...t, status: 'waiting_input', pending_question: { question, reason } } } };
  }),

  removeTask: (taskId) => set((s) => {
    const { [taskId]: _, ...rest } = s.tasks;
    return { tasks: rest, activeTaskId: s.activeTaskId === taskId ? null : s.activeTaskId };
  }),

  selectTask: (taskId) => set({ activeTaskId: taskId }),
  setSearch: (query) => set({ searchQuery: query }),
  setFilter: (status) => set({ statusFilter: status }),

  setCuration: (taskId, curation) => set((s) => {
    const t = s.tasks[taskId];
    if (!t) return s;
    return { tasks: { ...s.tasks, [taskId]: { ...t, curation } } };
  }),

  setGenerated: (taskId, generated) => set((s) => {
    const t = s.tasks[taskId];
    if (!t) return s;
    return { tasks: { ...s.tasks, [taskId]: { ...t, generated } } };
  }),
}));
