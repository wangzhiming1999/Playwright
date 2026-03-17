import { create } from 'zustand';
import type { Memory, MemoryStats } from '@/api/memory';
import { listMemories, getMemoryStats, deleteMemory, batchDeleteMemories } from '@/api/memory';

interface MemoryStore {
  memories: Memory[];
  stats: MemoryStats | null;
  loading: boolean;
  typeFilter: string;
  domainFilter: string;
  searchQuery: string;
  selectedIds: Set<string>;

  fetch: () => Promise<void>;
  fetchStats: () => Promise<void>;
  setTypeFilter: (type: string) => void;
  setDomainFilter: (domain: string) => void;
  setSearch: (query: string) => void;
  remove: (id: string) => Promise<void>;
  bulkRemove: () => Promise<void>;
  toggleSelect: (id: string) => void;
  clearSelection: () => void;
}

export const useMemoryStore = create<MemoryStore>((set, get) => ({
  memories: [],
  stats: null,
  loading: false,
  typeFilter: '',
  domainFilter: '',
  searchQuery: '',
  selectedIds: new Set(),

  fetch: async () => {
    set({ loading: true });
    try {
      const { typeFilter, domainFilter } = get();
      const data = await listMemories(domainFilter || undefined, typeFilter || undefined);
      set({ memories: data, loading: false });
    } catch {
      set({ loading: false });
    }
  },

  fetchStats: async () => {
    try {
      const stats = await getMemoryStats();
      set({ stats });
    } catch { /* ignore */ }
  },

  setTypeFilter: (type) => { set({ typeFilter: type }); get().fetch(); },
  setDomainFilter: (domain) => { set({ domainFilter: domain }); get().fetch(); },
  setSearch: (query) => set({ searchQuery: query }),

  remove: async (id) => {
    await deleteMemory(id);
    set((s) => ({ memories: s.memories.filter(m => m.id !== id) }));
    get().fetchStats();
  },

  bulkRemove: async () => {
    const ids = Array.from(get().selectedIds);
    if (!ids.length) return;
    await batchDeleteMemories(ids);
    set((s) => ({
      memories: s.memories.filter(m => !s.selectedIds.has(m.id)),
      selectedIds: new Set(),
    }));
    get().fetchStats();
  },

  toggleSelect: (id) => set((s) => {
    const next = new Set(s.selectedIds);
    if (next.has(id)) next.delete(id); else next.add(id);
    return { selectedIds: next };
  }),

  clearSelection: () => set({ selectedIds: new Set() }),
}));
