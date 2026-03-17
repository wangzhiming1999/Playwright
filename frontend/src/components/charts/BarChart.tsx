interface BarData {
  label: string;
  value: number;
  color?: string;
}

interface Props {
  data: BarData[];
  height?: number;
}

export function BarChart({ data, height = 160 }: Props) {
  const max = Math.max(...data.map((d) => d.value), 1);
  const barWidth = Math.min(40, Math.floor(260 / Math.max(data.length, 1)));
  const gap = 8;
  const svgWidth = data.length * (barWidth + gap) + gap;
  const padTop = 20;
  const padBottom = 28;
  const chartH = height - padTop - padBottom;

  if (data.length === 0) {
    return (
      <div className="chart-container" style={{ height, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>暂无数据</span>
      </div>
    );
  }

  return (
    <div className="chart-container">
      <svg width="100%" height={height} viewBox={`0 0 ${svgWidth} ${height}`} preserveAspectRatio="xMidYEnd meet">
        {data.map((d, i) => {
          const barH = (d.value / max) * chartH;
          const x = gap + i * (barWidth + gap);
          const y = padTop + chartH - barH;
          return (
            <g key={i}>
              <rect
                x={x}
                y={y}
                width={barWidth}
                height={barH}
                rx={3}
                fill={d.color || 'var(--accent)'}
                style={{ transition: 'height 0.4s, y 0.4s' }}
              >
                <title>{d.label}: {d.value}</title>
              </rect>
              <text
                x={x + barWidth / 2}
                y={y - 4}
                textAnchor="middle"
                fill="var(--text-muted)"
                fontSize="10"
              >
                {d.value}
              </text>
              <text
                x={x + barWidth / 2}
                y={height - 6}
                textAnchor="middle"
                fill="var(--text-muted)"
                fontSize="10"
              >
                {d.label}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
