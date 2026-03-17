/**
 * Workflow YAML ↔ React Flow 节点/边 转换工具
 */
import type { Node, Edge } from '@xyflow/react';

// 后端 block_type 对应的显示配置
export const BLOCK_TYPE_CONFIG: Record<string, { label: string; color: string; icon: string }> = {
  task:          { label: '任务',     color: '#3b82f6', icon: '🎯' },
  navigation:    { label: '导航',     color: '#10b981', icon: '🌐' },
  extraction:    { label: '提取',     color: '#8b5cf6', icon: '📊' },
  login:         { label: '登录',     color: '#f59e0b', icon: '🔑' },
  file_upload:   { label: '上传',     color: '#06b6d4', icon: '📤' },
  file_download: { label: '下载',     color: '#06b6d4', icon: '📥' },
  code:          { label: '代码',     color: '#ec4899', icon: '💻' },
  text_prompt:   { label: 'LLM',     color: '#6366f1', icon: '🤖' },
  http_request:  { label: 'HTTP',    color: '#14b8a6', icon: '🔗' },
  for_loop:      { label: '循环',     color: '#f97316', icon: '🔄' },
  conditional:   { label: '条件',     color: '#eab308', icon: '🔀' },
  wait:          { label: '等待',     color: '#64748b', icon: '⏳' },
};

export interface BlockData {
  block_type: string;
  label: string;
  [key: string]: unknown;
}

/**
 * 将 YAML 解析后的 blocks 数组转为 React Flow 的 nodes + edges
 */
export function blocksToFlow(blocks: BlockData[]): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = [];
  const edges: Edge[] = [];

  // 起始节点
  nodes.push({
    id: '__start__',
    type: 'startEnd',
    position: { x: 250, y: 0 },
    data: { label: '开始', isStart: true },
  });

  blocks.forEach((block, i) => {
    const config = BLOCK_TYPE_CONFIG[block.block_type] || { label: block.block_type, color: '#94a3b8', icon: '📦' };
    nodes.push({
      id: block.label || `block_${i}`,
      type: 'blockNode',
      position: { x: 250, y: 100 + i * 120 },
      data: {
        ...block,
        displayLabel: config.label,
        color: config.color,
        icon: config.icon,
        index: i,
      },
    });
  });

  // 结束节点
  nodes.push({
    id: '__end__',
    type: 'startEnd',
    position: { x: 250, y: 100 + blocks.length * 120 },
    data: { label: '结束', isStart: false },
  });

  // 连线：start → block[0] → block[1] → ... → end
  const nodeIds = nodes.map(n => n.id);
  for (let i = 0; i < nodeIds.length - 1; i++) {
    edges.push({
      id: `e_${nodeIds[i]}_${nodeIds[i + 1]}`,
      source: nodeIds[i],
      target: nodeIds[i + 1],
      type: 'smoothstep',
      animated: false,
    });
  }

  return { nodes, edges };
}

/**
 * 从 React Flow nodes 还原为 blocks 数组（保持顺序）
 * 按 y 坐标排序，过滤掉 start/end 节点
 */
export function flowToBlocks(nodes: Node[]): BlockData[] {
  return nodes
    .filter(n => n.type === 'blockNode')
    .sort((a, b) => a.position.y - b.position.y)
    .map(n => {
      const { displayLabel, color, icon, index, ...blockData } = n.data as Record<string, unknown>;
      return blockData as BlockData;
    });
}

/**
 * 新建一个 block 节点的默认数据
 */
export function createDefaultBlock(blockType: string, label: string): BlockData {
  const base: BlockData = { block_type: blockType, label };

  switch (blockType) {
    case 'navigation':
      return { ...base, url: 'https://example.com' };
    case 'task':
      return { ...base, task: '描述要执行的任务' };
    case 'extraction':
      return { ...base, goal: '提取目标', schema: {} };
    case 'login':
      return { ...base, site_key: 'example' };
    case 'code':
      return { ...base, code: '# Python code here\nresult = "hello"' };
    case 'text_prompt':
      return { ...base, prompt: '请回答以下问题...' };
    case 'http_request':
      return { ...base, url: 'https://api.example.com', method: 'GET' };
    case 'wait':
      return { ...base, seconds: 3 };
    case 'conditional':
      return { ...base, expression: 'true', then_blocks: [], else_blocks: [] };
    case 'for_loop':
      return { ...base, items: '[]', loop_blocks: [] };
    default:
      return base;
  }
}
