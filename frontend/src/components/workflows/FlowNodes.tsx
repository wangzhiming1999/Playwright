/**
 * React Flow 自定义节点：Block 节点 + 开始/结束节点
 */
import { memo, useState } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { BLOCK_TYPE_CONFIG } from './flowUtils';

/* ── Block 节点 ─────────────────────────────────────────────── */

interface BlockNodeData {
  block_type: string;
  label: string;
  displayLabel: string;
  color: string;
  icon: string;
  index: number;
  task?: string;
  url?: string;
  goal?: string;
  code?: string;
  prompt?: string;
  expression?: string;
  seconds?: number;
  site_key?: string;
  [key: string]: unknown;
}

export const BlockNode = memo(function BlockNode({ data, selected }: NodeProps) {
  const d = data as unknown as BlockNodeData;
  const [expanded, setExpanded] = useState(false);

  // 副标题：显示关键参数
  const subtitle = d.url || d.task || d.goal || d.prompt || d.code?.slice(0, 40) || d.expression || (d.seconds ? `${d.seconds}s` : '') || d.site_key || '';

  return (
    <div
      className={`flow-block-node ${selected ? 'selected' : ''}`}
      style={{ '--block-color': d.color } as React.CSSProperties}
      onClick={() => setExpanded(!expanded)}
    >
      <Handle type="target" position={Position.Top} className="flow-handle" />

      <div className="flow-block-header">
        <span className="flow-block-icon">{d.icon}</span>
        <span className="flow-block-type">{d.displayLabel}</span>
        <span className="flow-block-label">{d.label}</span>
      </div>

      {subtitle && (
        <div className="flow-block-subtitle" title={subtitle}>
          {subtitle.length > 50 ? subtitle.slice(0, 50) + '...' : subtitle}
        </div>
      )}

      {expanded && (
        <div className="flow-block-details">
          {Object.entries(d)
            .filter(([k]) => !['displayLabel', 'color', 'icon', 'index', 'block_type', 'label'].includes(k))
            .filter(([, v]) => v !== undefined && v !== '' && v !== null)
            .map(([k, v]) => (
              <div key={k} className="flow-block-field">
                <span className="flow-field-key">{k}:</span>
                <span className="flow-field-value">
                  {typeof v === 'object' ? JSON.stringify(v).slice(0, 60) : String(v).slice(0, 60)}
                </span>
              </div>
            ))}
        </div>
      )}

      <Handle type="source" position={Position.Bottom} className="flow-handle" />
    </div>
  );
});

/* ── 开始/结束节点 ──────────────────────────────────────────── */

interface StartEndData {
  label: string;
  isStart: boolean;
}

export const StartEndNode = memo(function StartEndNode({ data }: NodeProps) {
  const d = data as unknown as StartEndData;
  return (
    <div className={`flow-start-end-node ${d.isStart ? 'start' : 'end'}`}>
      {!d.isStart && <Handle type="target" position={Position.Top} className="flow-handle" />}
      <span>{d.label}</span>
      {d.isStart && <Handle type="source" position={Position.Bottom} className="flow-handle" />}
    </div>
  );
});

/* ── 节点类型注册表 ─────────────────────────────────────────── */

export const nodeTypes = {
  blockNode: BlockNode,
  startEnd: StartEndNode,
};

/* ── 节点工具栏（添加新 block 的面板） ──────────────────────── */

interface NodePaletteProps {
  onAdd: (blockType: string) => void;
}

export function NodePalette({ onAdd }: NodePaletteProps) {
  return (
    <div className="flow-palette">
      <div className="flow-palette-title">节点库</div>
      <div className="flow-palette-grid">
        {Object.entries(BLOCK_TYPE_CONFIG).map(([type, config]) => (
          <button
            key={type}
            className="flow-palette-item"
            onClick={() => onAdd(type)}
            title={type}
            style={{ '--block-color': config.color } as React.CSSProperties}
          >
            <span className="flow-palette-icon">{config.icon}</span>
            <span className="flow-palette-label">{config.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
