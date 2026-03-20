import { useState, useEffect } from 'react';
import { getTaskTrace } from '../../api/tasks';
import type { TaskTrace, StepTrace } from '../../types/task';

const TOOL_COLORS: Record<string, string> = {
  navigate: '#3b82f6',
  click: '#22c55e',
  type_text: '#a855f7',
  scroll: '#6b7280',
  done: '#06b6d4',
  screenshot: '#f59e0b',
  wait: '#94a3b8',
  extract: '#ec4899',
  select_option: '#f97316',
  press_key: '#8b5cf6',
};

function toolColor(name: string) {
  return TOOL_COLORS[name] ?? '#64748b';
}

function fmtMs(ms: number) {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function fmtCost(usd: number) {
  if (usd < 0.001) return '<$0.001';
  return `$${usd.toFixed(4)}`;
}

function argsSummary(args: Record<string, unknown>): string {
  const entries = Object.entries(args).filter(([, v]) => v !== null && v !== undefined && v !== '');
  if (entries.length === 0) return '';
  const [k, v] = entries[0];
  const str = typeof v === 'string' ? v : JSON.stringify(v);
  const truncated = str.length > 60 ? str.slice(0, 60) + '…' : str;
  return `${k}: ${truncated}`;
}

function StepRow({ step, index }: { step: StepTrace; index: number }) {
  const [open, setOpen] = useState(false);
  const isError = step.result_is_error;
  const isVerifyFail = !step.verify_changed && step.verify_type !== 'skip' && step.verify_type !== 'unknown';

  const rowBg = isError
    ? 'var(--color-error-bg, rgba(239,68,68,0.08))'
    : isVerifyFail
    ? 'var(--color-warn-bg, rgba(245,158,11,0.08))'
    : 'transparent';

  return (
    <div
      style={{ borderBottom: '1px solid var(--border)', background: rowBg }}
    >
      <div
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '8px 12px',
          cursor: 'pointer',
          userSelect: 'none',
        }}
      >
        <span style={{ color: 'var(--text-muted)', fontSize: 12, minWidth: 28 }}>
          {index + 1}
        </span>
        <span style={{ fontSize: 11 }}>
          {step.input_mode === 'screenshot' ? '📷' : '📄'}
        </span>
        <span
          style={{
            background: toolColor(step.tool_name),
            color: '#fff',
            borderRadius: 4,
            padding: '1px 7px',
            fontSize: 11,
            fontWeight: 600,
            minWidth: 80,
            textAlign: 'center',
          }}
        >
          {step.tool_name}
        </span>
        <span style={{ flex: 1, fontSize: 12, color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {argsSummary(step.tool_args)}
        </span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)', minWidth: 48, textAlign: 'right' }}>
          {fmtMs(step.duration_ms)}
        </span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)', minWidth: 56, textAlign: 'right' }}>
          {step.input_tokens + step.output_tokens}t
        </span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)', minWidth: 60, textAlign: 'right' }}>
          {fmtCost(step.cost_usd)}
        </span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 4 }}>
          {open ? '▲' : '▼'}
        </span>
      </div>

      {open && (
        <div style={{ padding: '8px 16px 12px 48px', fontSize: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {Object.keys(step.tool_args).length > 0 && (
            <div>
              <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>参数：</span>
              <pre style={{ margin: '2px 0 0', padding: '6px 8px', background: 'var(--bg-secondary)', borderRadius: 4, fontSize: 11, overflowX: 'auto' }}>
                {JSON.stringify(step.tool_args, null, 2)}
              </pre>
            </div>
          )}
          <div>
            <span style={{ color: isError ? '#ef4444' : 'var(--text-muted)', fontWeight: 600 }}>
              {isError ? '错误：' : '结果：'}
            </span>
            <span style={{ color: isError ? '#ef4444' : 'var(--text)', marginLeft: 4 }}>
              {step.result || '—'}
            </span>
          </div>
          {(step.url_before || step.url_after) && step.url_before !== step.url_after && (
            <div>
              <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>URL 变化：</span>
              <span style={{ color: 'var(--text-muted)', marginLeft: 4, fontSize: 11 }}>
                {step.url_before} → {step.url_after}
              </span>
            </div>
          )}
          {step.verify_type && step.verify_type !== 'skip' && (
            <div>
              <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>验证：</span>
              <span style={{ color: step.verify_changed ? '#22c55e' : '#f59e0b', marginLeft: 4 }}>
                {step.verify_type} {step.verify_changed ? '✓ 有变化' : '✗ 无变化'}
              </span>
              {step.verify_nudge && (
                <span style={{ color: '#f59e0b', marginLeft: 8 }}>{step.verify_nudge}</span>
              )}
            </div>
          )}
          {step.nudges.length > 0 && (
            <div>
              <span style={{ color: '#f59e0b', fontWeight: 600 }}>提醒：</span>
              {step.nudges.map((n, i) => (
                <div key={i} style={{ color: '#f59e0b', marginLeft: 8, fontSize: 11 }}>• {n}</div>
              ))}
            </div>
          )}
          {step.events.length > 0 && (
            <div>
              <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>事件：</span>
              {step.events.map((e, i) => (
                <div key={i} style={{ color: 'var(--text-muted)', marginLeft: 8, fontSize: 11 }}>• {e}</div>
              ))}
            </div>
          )}
          <div style={{ color: 'var(--text-muted)', fontSize: 11 }}>
            模型：{step.model || '—'} | 输入：{step.input_tokens}t | 输出：{step.output_tokens}t | 缓存：{step.cached_tokens}t
          </div>
        </div>
      )}
    </div>
  );
}

export default function TraceView({ taskId }: { taskId: string }) {
  const [trace, setTrace] = useState<TaskTrace | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    getTaskTrace(taskId)
      .then(setTrace)
      .catch(e => setError(e?.message ?? '加载失败'))
      .finally(() => setLoading(false));
  }, [taskId]);

  if (loading) return <div style={{ padding: 24, color: 'var(--text-muted)' }}>加载追踪数据…</div>;
  if (error) return <div style={{ padding: 24, color: '#ef4444' }}>暂无追踪数据（{error}）</div>;
  if (!trace) return null;

  const errorSteps = trace.steps.filter(s => s.result_is_error).length;
  const totalDuration = trace.steps.reduce((sum, s) => sum + s.duration_ms, 0);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* 摘要卡片 */}
      <div style={{ display: 'flex', gap: 12, padding: '12px 16px', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
        {[
          { label: '总步数', value: trace.total_steps },
          { label: '总耗时', value: fmtMs(totalDuration) },
          { label: '总成本', value: fmtCost(trace.total_cost_usd) },
          { label: '错误步', value: errorSteps, warn: errorSteps > 0 },
        ].map(card => (
          <div
            key={card.label}
            style={{
              flex: 1,
              background: 'var(--bg-secondary)',
              borderRadius: 8,
              padding: '10px 14px',
              textAlign: 'center',
            }}
          >
            <div style={{ fontSize: 20, fontWeight: 700, color: card.warn ? '#ef4444' : 'var(--text)' }}>
              {card.value}
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>{card.label}</div>
          </div>
        ))}
      </div>

      {/* 列表表头 */}
      <div style={{
        display: 'flex',
        gap: 8,
        padding: '6px 12px',
        borderBottom: '1px solid var(--border)',
        fontSize: 11,
        color: 'var(--text-muted)',
        fontWeight: 600,
        flexShrink: 0,
      }}>
        <span style={{ minWidth: 28 }}>#</span>
        <span style={{ minWidth: 16 }} />
        <span style={{ minWidth: 80 }}>工具</span>
        <span style={{ flex: 1 }}>参数</span>
        <span style={{ minWidth: 48, textAlign: 'right' }}>耗时</span>
        <span style={{ minWidth: 56, textAlign: 'right' }}>Token</span>
        <span style={{ minWidth: 60, textAlign: 'right' }}>成本</span>
        <span style={{ minWidth: 16 }} />
      </div>

      {/* 步骤列表 */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {trace.steps.length === 0 ? (
          <div style={{ padding: 24, color: 'var(--text-muted)', textAlign: 'center' }}>暂无步骤数据</div>
        ) : (
          trace.steps.map((step, i) => <StepRow key={i} step={step} index={i} />)
        )}
      </div>
    </div>
  );
}
