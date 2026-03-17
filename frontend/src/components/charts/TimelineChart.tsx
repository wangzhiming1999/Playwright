interface DataPoint {
  date: string;
  success: number;
  failed: number;
}

interface Props {
  data: DataPoint[];
  height?: number;
}

export function TimelineChart({ data, height = 160 }: Props) {
  if (data.length === 0) {
    return (
      <div className="chart-container" style={{ height, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <span style={{ color: 'var(--text-muted)', fontSize: 13 }}>暂无数据</span>
      </div>
    );
  }

  const padLeft = 30;
  const padRight = 10;
  const padTop = 16;
  const padBottom = 28;
  const svgWidth = 400;
  const chartW = svgWidth - padLeft - padRight;
  const chartH = height - padTop - padBottom;

  const maxVal = Math.max(...data.map((d) => d.success + d.failed), 1);

  function x(i: number) {
    return padLeft + (data.length === 1 ? chartW / 2 : (i / (data.length - 1)) * chartW);
  }
  function y(val: number) {
    return padTop + chartH - (val / maxVal) * chartH;
  }

  const successPoints = data.map((d, i) => `${x(i)},${y(d.success)}`).join(' ');
  const failedPoints = data.map((d, i) => `${x(i)},${y(d.failed)}`).join(' ');

  // Area fill paths
  const successArea = `M${x(0)},${y(0)} ` + data.map((d, i) => `L${x(i)},${y(d.success)}`).join(' ') + ` L${x(data.length - 1)},${y(0)} Z`;
  const failedArea = `M${x(0)},${y(0)} ` + data.map((d, i) => `L${x(i)},${y(d.failed)}`).join(' ') + ` L${x(data.length - 1)},${y(0)} Z`;

  // Y-axis ticks
  const yTicks = [0, Math.round(maxVal / 2), maxVal];

  return (
    <div className="chart-container">
      <svg width="100%" height={height} viewBox={`0 0 ${svgWidth} ${height}`} preserveAspectRatio="xMidYMid meet">
        {/* Grid lines */}
        {yTicks.map((tick) => (
          <g key={tick}>
            <line x1={padLeft} y1={y(tick)} x2={svgWidth - padRight} y2={y(tick)} stroke="var(--border)" strokeDasharray="3,3" />
            <text x={padLeft - 4} y={y(tick) + 3} textAnchor="end" fill="var(--text-muted)" fontSize="9">{tick}</text>
          </g>
        ))}

        {/* Areas */}
        <path d={successArea} fill="var(--green)" opacity={0.15} />
        <path d={failedArea} fill="var(--red)" opacity={0.15} />

        {/* Lines */}
        <polyline points={successPoints} fill="none" stroke="var(--green)" strokeWidth={2} strokeLinejoin="round" />
        <polyline points={failedPoints} fill="none" stroke="var(--red)" strokeWidth={2} strokeLinejoin="round" />

        {/* Dots */}
        {data.map((d, i) => (
          <g key={i}>
            <circle cx={x(i)} cy={y(d.success)} r={3} fill="var(--green)">
              <title>{d.date}: {d.success} 成功</title>
            </circle>
            {d.failed > 0 && (
              <circle cx={x(i)} cy={y(d.failed)} r={3} fill="var(--red)">
                <title>{d.date}: {d.failed} 失败</title>
              </circle>
            )}
            <text x={x(i)} y={height - 6} textAnchor="middle" fill="var(--text-muted)" fontSize="9">
              {d.date.slice(5)}
            </text>
          </g>
        ))}
      </svg>
      <div className="chart-legend">
        <span className="chart-legend-item"><i className="chart-legend-dot" style={{ background: 'var(--green)' }} />成功</span>
        <span className="chart-legend-item"><i className="chart-legend-dot" style={{ background: 'var(--red)' }} />失败</span>
      </div>
    </div>
  );
}
