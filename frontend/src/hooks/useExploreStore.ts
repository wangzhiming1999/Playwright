import { create } from 'zustand';
import type { ExploreTask } from '@/types/explore';

interface ExploreStore {
  tasks: Record<string, ExploreTask>;
  activeEid: string | null;

  setSnapshot: (tasks: ExploreTask[]) => void;
  addTask: (task: ExploreTask) => void;
  updateStatus: (eid: string, status: ExploreTask['status'], extra?: Partial<ExploreTask>) => void;
  appendLog: (eid: string, message: string) => void;
  removeTask: (eid: string) => void;
  selectTask: (eid: string | null) => void;
  setCuration: (eid: string, curation: ExploreTask['curation']) => void;
  setGenerated: (eid: string, generated: ExploreTask['generated']) => void;
}

export const useExploreStore = create<ExploreStore>((set) => ({
  tasks: {},
  activeEid: null,

  setSnapshot: (tasks) => set({
    tasks: Object.fromEntries(tasks.map(t => [t.eid, { ...t, logs: t.logs || [], screenshots: t.screenshots || [] }])),
  }),

  addTask: (task) => set((s) => ({
    tasks: { ...s.tasks, [task.eid]: { ...task, logs: task.logs || [], screenshots: task.screenshots || [] } },
  })),

  updateStatus: (eid, status, extra) => set((s) => {
    const t = s.tasks[eid];
    if (!t) return s;
    return { tasks: { ...s.tasks, [eid]: { ...t, ...extra, status } } };
  }),

  appendLog: (eid, message) => set((s) => {
    const t = s.tasks[eid];
    if (!t) return s;
    return { tasks: { ...s.tasks, [eid]: { ...t, logs: [...t.logs, message] } } };
  }),

  removeTask: (eid) => set((s) => {
    const { [eid]: _, ...rest } = s.tasks;
    return { tasks: rest, activeEid: s.activeEid === eid ? null : s.activeEid };
  }),

  selectTask: (eid) => set({ activeEid: eid }),

  setCuration: (eid, curation) => set((s) => {
    const t = s.tasks[eid];
    if (!t) return s;
    return { tasks: { ...s.tasks, [eid]: { ...t, curation } } };
  }),

  setGenerated: (eid, generated) => set((s) => {
    const t = s.tasks[eid];
    if (!t) return s;
    return { tasks: { ...s.tasks, [eid]: { ...t, generated } } };
  }),
}));
