import { create } from 'zustand';
import type { Recording, RecordedAction } from '@/api/recordings';
import { listRecordings, deleteRecording } from '@/api/recordings';

interface RecordingStore {
  recordings: Recording[];
  activeRecordingId: string | null;
  loading: boolean;

  fetch: () => Promise<void>;
  addRecording: (r: Recording) => void;
  updateRecording: (id: string, patch: Partial<Recording>) => void;
  appendAction: (recordingId: string, action: RecordedAction) => void;
  remove: (id: string) => Promise<void>;
  setActive: (id: string | null) => void;
}

export const useRecordingStore = create<RecordingStore>((set, get) => ({
  recordings: [],
  activeRecordingId: null,
  loading: false,

  fetch: async () => {
    set({ loading: true });
    try {
      const data = await listRecordings();
      set({ recordings: data, loading: false });
    } catch {
      set({ loading: false });
    }
  },

  addRecording: (r) => set((s) => ({ recordings: [r, ...s.recordings] })),

  updateRecording: (id, patch) => set((s) => ({
    recordings: s.recordings.map(r => r.id === id ? { ...r, ...patch } : r),
  })),

  appendAction: (recordingId, action) => set((s) => ({
    recordings: s.recordings.map(r =>
      r.id === recordingId ? { ...r, actions: [...r.actions, action] } : r
    ),
  })),

  remove: async (id) => {
    await deleteRecording(id);
    set((s) => ({
      recordings: s.recordings.filter(r => r.id !== id),
      activeRecordingId: s.activeRecordingId === id ? null : s.activeRecordingId,
    }));
  },

  setActive: (id) => set({ activeRecordingId: id }),
}));
