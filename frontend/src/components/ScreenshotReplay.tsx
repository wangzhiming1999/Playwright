import { useState, useEffect, useCallback, useMemo } from 'react';
import { parseLogSteps } from '@/utils/logParser';
import './ScreenshotReplay.css';

interface Props {
  screenshots: string[];
  screenshotPrefix: string;
  logs?: string[];
}

const STEP_RE = /step_(\d+)_annotated/;

function extractStepNum(filename: string): number {
  const m = filename.match(STEP_RE);
  return m ? parseInt(m[1], 10) : -1;
}

export function ScreenshotReplay({ screenshots, screenshotPrefix, logs }: Props) {
  const [mode, setMode] = useState<'timeline' | 'grid'>(screenshots.length > 1 ? 'timeline' : 'grid');
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(2000);
  const [lightbox, setLightbox] = useState<string | null>(null);

  const sorted = useMemo(
    () => [...screenshots].sort((a, b) => extractStepNum(a) - extractStepNum(b)),
    [screenshots],
  );

  const logSteps = useMemo(() => (logs ? parseLogSteps(logs) : []), [logs]);

  const currentFile = sorted[index] || '';
  const currentStepNum = extractStepNum(currentFile);
  const currentLogStep = logSteps.find((s) => s.stepNumber === currentStepNum);

  // Auto-play
  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => {
      setIndex((prev) => {
        if (prev >= sorted.length - 1) { setPlaying(false); return prev; }
        return prev + 1;
      });
    }, speed);
    return () => clearInterval(timer);
  }, [playing, speed, sorted.length]);

  // Preload adjacent images
  useEffect(() => {
    [index - 1, index + 1].forEach((i) => {
      if (i >= 0 && i < sorted.length) {
        const img = new Image();
        img.src = `${screenshotPrefix}/${sorted[i]}`;
      }
    });
  }, [index, sorted, screenshotPrefix]);

  // Keyboard navigation
  const handleKey = useCallback((e: KeyboardEvent) => {
    if (mode !== 'timeline') return;
    if (e.key === 'ArrowLeft') setIndex((p) => Math.max(0, p - 1));
    else if (e.key === 'ArrowRight') setIndex((p) => Math.min(sorted.length - 1, p + 1));
    else if (e.key === ' ') { e.preventDefault(); setPlaying((p) => !p); }
    else if (e.key === 'Escape' && lightbox) setLightbox(null);
  }, [mode, sorted.length, lightbox]);

  useEffect(() => {
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [handleKey]);

  if (screenshots.length === 0) {
    return <div className="empty-state">暂无截图</div>;
  }

  return (
    <div className="replay">
      <div className="replay-mode-bar">
        <button className={`replay-mode-btn ${mode === 'timeline' ? 'active' : ''}`} onClick={() => setMode('timeline')}>
          时间轴
        </button>
        <button className={`replay-mode-btn ${mode === 'grid' ? 'active' : ''}`} onClick={() => setMode('grid')}>
          网格
        </button>
      </div>

      {mode === 'grid' ? (
        <div className="screenshot-grid">
          {sorted.map((filename) => (
            <div key={filename} className="screenshot-card" onClick={() => setLightbox(filename)}>
              <img src={`${screenshotPrefix}/${filename}`} alt={filename} loading="lazy" />
              <div className="screenshot-card-name">{filename}</div>
            </div>
          ))}
        </div>
      ) : (
        <div className="replay-timeline">
          <div className="replay-main">
            <div className="replay-viewer">
              <img
                src={`${screenshotPrefix}/${currentFile}`}
                alt={currentFile}
                onClick={() => setLightbox(currentFile)}
              />
            </div>
            {currentLogStep && (
              <div className="replay-log-panel">
                <div className="replay-log-title">{currentLogStep.label}</div>
                <div className="replay-log-lines">
                  {currentLogStep.lines.map((line, i) => (
                    <div key={i} className="replay-log-line">{line}</div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="replay-controls">
            <button className="replay-btn" onClick={() => setIndex((p) => Math.max(0, p - 1))} disabled={index === 0}>
              ◀
            </button>
            <button className="replay-btn replay-play" onClick={() => setPlaying(!playing)}>
              {playing ? '⏸' : '▶'}
            </button>
            <button className="replay-btn" onClick={() => setIndex((p) => Math.min(sorted.length - 1, p + 1))} disabled={index >= sorted.length - 1}>
              ▶
            </button>

            <span className="replay-indicator">{index + 1} / {sorted.length}</span>

            <div className="replay-progress" onClick={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              const pct = (e.clientX - rect.left) / rect.width;
              setIndex(Math.round(pct * (sorted.length - 1)));
            }}>
              <div className="replay-progress-fill" style={{ width: `${((index + 1) / sorted.length) * 100}%` }} />
            </div>

            <select className="replay-speed" value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
              <option value={1000}>1s</option>
              <option value={2000}>2s</option>
              <option value={3000}>3s</option>
              <option value={5000}>5s</option>
            </select>
          </div>
        </div>
      )}

      {lightbox && (
        <div className="lightbox-overlay" onClick={() => setLightbox(null)}>
          <button className="lightbox-close" onClick={() => setLightbox(null)}>×</button>
          <img src={`${screenshotPrefix}/${lightbox}`} alt={lightbox} onClick={(e) => e.stopPropagation()} />
        </div>
      )}
    </div>
  );
}
