import { useEffect } from 'react';

export function useKeyboardShortcuts() {
  useEffect(() => {
    function handler(e: KeyboardEvent) {
      // Ctrl/Cmd+K: focus search input
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        const input = document.querySelector<HTMLInputElement>(
          '.task-search, .logviewer-search, input[placeholder*="搜索"]'
        );
        input?.focus();
      }

      // Escape: close overlays
      if (e.key === 'Escape') {
        const overlay = document.querySelector<HTMLElement>('.lightbox-overlay');
        overlay?.click();
      }
    }

    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);
}
