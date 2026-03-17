import { create } from 'zustand';

type Theme = 'dark' | 'light';

interface ThemeStore {
  theme: Theme;
  toggle: () => void;
}

const stored = localStorage.getItem('theme') as Theme | null;
const initial: Theme = stored === 'light' ? 'light' : 'dark';
document.documentElement.dataset.theme = initial;

export const useThemeStore = create<ThemeStore>((set) => ({
  theme: initial,
  toggle: () =>
    set((s) => {
      const next: Theme = s.theme === 'dark' ? 'light' : 'dark';
      localStorage.setItem('theme', next);
      document.documentElement.dataset.theme = next;
      return { theme: next };
    }),
}));
