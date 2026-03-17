/**
 * Workflow 可视化编辑器主组件
 * 基于 @xyflow/react，支持拖拽编排、节点增删、YAML 双向同步
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  addEdge,
  type Connection,
  type Node,
  type Edge,
  BackgroundVariant,
  Panel,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { nodeTypes, NodePalette } from './FlowNodes';
import { blocksToFlow, flowToBlocks, createDefaultBlock, type BlockData } from './flowUtils';

interface WorkflowEditorProps {
  blocks: BlockData[];
  onChange: (blocks: BlockData[]) => void;
  title?: string;
}

export function WorkflowEditor({ blocks, onChange, title }: WorkflowEditorProps) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [labelCounter, setLabelCounter] = useState(blocks.length + 1);

  // blocks → nodes/edges（初始化 + 外部变更时同步）
  useEffect(() => {
    const { nodes: n, edges: e } = blocksToFlow(blocks);
    setNodes(n);
    setEdges(e);
  }, [blocks, setNodes, setEdges]);

  // 连线
  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) => addEdge({ ...params, type: 'smoothstep' }, eds)),
    [setEdges],
  );

  // 节点选中
  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    setSelectedNode(node.id);
  }, []);

  // 添加新节点
  const handleAddBlock = useCallback((blockType: string) => {
    const label = `step_${labelCounter}`;
    setLabelCounter(c => c + 1);

    const newBlock = createDefaultBlock(blockType, label);
    const updatedBlocks = [...flowToBlocks(nodes), newBlock];
    onChange(updatedBlocks);
  }, [nodes, onChange, labelCounter]);

  // 删除选中节点
  const handleDeleteSelected = useCallback(() => {
    if (!selectedNode || selectedNode === '__start__' || selectedNode === '__end__') return;
    const updatedBlocks = flowToBlocks(nodes).filter(b => b.label !== selectedNode);
    onChange(updatedBlocks);
    setSelectedNode(null);
  }, [selectedNode, nodes, onChange]);

  // 拖拽结束后同步顺序到 blocks
  const onNodeDragStop = useCallback(() => {
    const updatedBlocks = flowToBlocks(nodes);
    onChange(updatedBlocks);
  }, [nodes, onChange]);

  // 键盘快捷键
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedNode) {
        // 只在非输入框时触发
        if ((e.target as HTMLElement).tagName !== 'INPUT' && (e.target as HTMLElement).tagName !== 'TEXTAREA') {
          handleDeleteSelected();
        }
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedNode, handleDeleteSelected]);

  // MiniMap 节点颜色
  const miniMapNodeColor = useCallback((node: Node) => {
    if (node.type === 'startEnd') return '#64748b';
    const color = (node.data as Record<string, unknown>)?.color;
    return (typeof color === 'string' ? color : '#94a3b8');
  }, []);

  return (
    <div className="flow-editor-container">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={onNodeClick}
        onNodeDragStop={onNodeDragStop}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.3 }}
        deleteKeyCode={null}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="var(--text-muted)" />
        <Controls showInteractive={false} />
        <MiniMap
          nodeColor={miniMapNodeColor}
          maskColor="rgba(0,0,0,0.1)"
          style={{ background: 'var(--bg-secondary)', border: '1px solid var(--border)' }}
        />

        {/* 顶部工具栏 */}
        <Panel position="top-left">
          <div className="flow-toolbar">
            {title && <span className="flow-toolbar-title">{title}</span>}
            {selectedNode && selectedNode !== '__start__' && selectedNode !== '__end__' && (
              <button className="flow-toolbar-btn danger" onClick={handleDeleteSelected} title="删除选中节点 (Delete)">
                删除节点
              </button>
            )}
          </div>
        </Panel>

        {/* 右侧节点面板 */}
        <Panel position="top-right">
          <NodePalette onAdd={handleAddBlock} />
        </Panel>
      </ReactFlow>
    </div>
  );
}
