import { useState, useRef, useEffect, useMemo } from 'react';
import { parseLogSteps, getLogLevel, matchesSearch } from '@/utils/logParser';
import type { LogLevel } from '@/utils/logParser';
import './LogViewer.css';

interface Props {
  logs: string[];
  isLive?: boolean;
}

const LEVEL_LABELS: Record<LogLevel, string> = {
  action: '操作',
  ok: '成功',
  warn: '警告',
  err: '错误',
  progress: '进度',
  '': '其他',
};

const LEVEL_CSS: Record<LogLevel, string> = {
  action: 'log-action',
  ok: 'log-ok',
  warn: 'log-warn',
  err: 'log-err',
  progress: 'log-progress',
  '': '',
};

export function LogViewer({ logs, isLive = false }: Props) {
  const [search, setSearch] = useState('');
  const [hiddenLevels, setHiddenLevels] = useState<Set<LogLevel>>(new Set(['progress']));
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());
  const [autoScroll, setAutoScroll] = useState(isLive);
  const endRef = useRef<HTMLDivElement>(null);

  const steps = useMemo(() => parseLogSteps(logs), [logs]);

  useEffect(() => {
    if (autoScroll) endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs.length, autoScroll]);

  function toggleLevel(level: LogLevel) {
    setHiddenLevels((prev) => {
      const next = new Set(prev);
      if (next.has(level)) next.delete(level); else next.add(level);
      return next;
    });
  }

  function toggleCollapse(stepNum: number) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(stepNum)) next.delete(stepNum); else next.add(stepNum);
      return next;
    });
  }

  function highlightMatch(text: string) {
    if (!search) return text;
    const idx = text.toLowerCase().indexOf(search.toLowerCase());
    if (idx === -1) return text;
    return (
      <>
        {text.slice(0, idx)}
        <mark className="log-highlight">{text.slice(idx, idx + search.length)}</mark>
        {text.slice(idx + search.length)}
      </>
    );
  }

  return (
    <div className="logviewer">
      <div className="logviewer-toolbar">
        <input
          className="logviewer-search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="搜索日志..."
        />
        <div className="logviewer-filters">
          {(['action', 'ok', 'warn', 'err'] as LogLevel[]).map((lv) => (
            <button
              key={lv}
              className={`logviewer-chip ${LEVEL_CSS[lv]} ${hiddenLevels.has(lv) ? 'chip-off' : ''}`}
              onClick={() => toggleLevel(lv)}
            >
              {LEVEL_LABELS[lv]}
            </button>
          ))}
        </div>
        <button
          className={`logviewer-autoscroll ${autoScroll ? 'active' : ''}`}
          onClick={() => setAutoScroll(!autoScroll)}
          title={autoScroll ? '自动滚动：开' : '自动滚动：关'}
        >
          ↓
        </button>
      </div>

      <div className="logviewer-body">
        {steps.map((step) => {
          const isCollapsed = collapsed.has(step.stepNumber);
          const filteredLines = step.lines.filter((line) => {
            const level = getLogLevel(line);
            if (hiddenLevels.has(level)) return false;
            if (search && !matchesSearch(line, search)) return false;
            return true;
          });

          if (filteredLines.length === 0 && search) return null;

          return (
            <div key={step.stepNumber} className="logviewer-step">
              <div
                className="logviewer-step-header"
                onClick={() => toggleCollapse(step.stepNumber)}
              >
                <span className={`logviewer-chevron ${isCollapsed ? '' : 'open'}`}>▶</span>
                <span className="logviewer-step-label">{step.label}</span>
                <span className="logviewer-step-count">{step.lines.length} 行</span>
              </div>
              {!isCollapsed && (
                <div className="logviewer-step-body">
                  {filteredLines.map((line, i) => (
                    <div key={i} className={`log-line ${LEVEL_CSS[getLogLevel(line)]}`}>
                      {highlightMatch(line)}
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
        <div ref={endRef} />
      </div>
    </div>
  );
}
