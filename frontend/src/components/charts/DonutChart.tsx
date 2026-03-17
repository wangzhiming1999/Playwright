import { useMemo } from 'react';

interface Segment {
  value: number;
  color: string;
  label: string;
}

interface Props {
  segments: Segment[];
  size?: number;
}

export function DonutChart({ segments, size = 180 }: Props) {
  const total = segments.reduce((s, seg) => s + seg.value, 0);
  const r = (size - 20) / 2;
  const cx = size / 2;
  const cy = size / 2;
  const circumference = 2 * Math.PI * r;

  const arcs = useMemo(() => {
    const filtered = segments.filter((s) => s.value > 0);
    const result = [];
    let runningOffset = 0;
    for (const seg of filtered) {
      const pct = seg.value / total;
      const dash = pct * circumference;
      result.push({ ...seg, dash, gap: circumference - dash, offset: runningOffset });
      runningOffset += dash;
    }
    return result;
  }, [segments, total, circumference]);

  if (total === 0) {
    return (
      <div className="chart-container" style={{ width: size, height: size, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>暂无数据</span>
      </div>
    );
  }

  return (
    <div className="chart-container">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        {arcs.map((arc, i) => (
          <circle
            key={i}
            cx={cx}
            cy={cy}
            r={r}
            fill="none"
            stroke={arc.color}
            strokeWidth={16}
            strokeDasharray={`${arc.dash} ${arc.gap}`}
            strokeDashoffset={-arc.offset}
            transform={`rotate(-90 ${cx} ${cy})`}
            style={{ transition: 'stroke-dasharray 0.5s, stroke-dashoffset 0.5s' }}
          >
            <title>{arc.label}: {arc.value}</title>
          </circle>
        ))}
        <text x={cx} y={cy - 6} textAnchor="middle" fill="var(--text-primary)" fontSize="22" fontWeight="700">
          {total}
        </text>
        <text x={cx} y={cy + 14} textAnchor="middle" fill="var(--text-muted)" fontSize="11">
          总计
        </text>
      </svg>
      <div className="chart-legend">
        {arcs.map((arc, i) => (
          <span key={i} className="chart-legend-item">
            <i style={{ background: arc.color }} className="chart-legend-dot" />
            {arc.label} {arc.value}
          </span>
        ))}
      </div>
    </div>
  );
}
