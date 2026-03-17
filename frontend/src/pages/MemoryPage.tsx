import { useEffect, useState } from 'react';
import { useMemoryStore } from '@/hooks/useMemoryStore';
import { updateMemory } from '@/api/memory';
import type { Memory } from '@/api/memory';
import './MemoryPage.css';

const TYPE_LABELS: Record<string, string> = {
  site: '站点经验',
  pattern: '操作模式',
  failure: '失败教训',
};

const TYPE_ICONS: Record<string, string> = {
  site: '\uD83C\uDF10',
  pattern: '\u2699\uFE0F',
  failure: '\u26A0\uFE0F',
};

function MemoryCard({ memory, selected, onToggle, onDelete, onEdit }: {
  memory: Memory; selected: boolean;
  onToggle: () => void; onDelete: () => void; onEdit: (m: Memory) => void;
}) {
  return (
    <div className={`memory-card ${selected ? 'selected' : ''}`}>
      <div className="memory-card-header">
        <label className="memory-checkbox">
          <input type="checkbox" checked={selected} onChange={onToggle} />
        </label>
        <span className="memory-type-badge" data-type={memory.memory_type}>
          {TYPE_ICONS[memory.memory_type]} {TYPE_LABELS[memory.memory_type] || memory.memory_type}
        </span>
        {memory.domain && <span className="memory-domain">{memory.domain}</span>}
        <span className="memory-hits" title="命中次数">{memory.hit_count}x</span>
      </div>
      <div className="memory-card-title">{memory.title}</div>
      <div className="memory-card-content">
        <pre>{JSON.stringify(memory.content, null, 2)}</pre>
      </div>
      <div className="memory-card-footer">
        <span className="memory-date">{memory.created_at?.slice(0, 16)}</span>
        {memory.last_used_at && <span className="memory-used">上次使用: {memory.last_used_at.slice(0, 16)}</span>}
        <div className="memory-actions">
          <button className="btn-sm" onClick={() => onEdit(memory)}>编辑</button>
          <button className="btn-sm btn-danger" onClick={onDelete}>删除</button>
        </div>
      </div>
    </div>
  );
}

// PLACEHOLDER_EDIT_MODAL

export function MemoryPage() {
  const {
    memories, stats, loading, typeFilter, searchQuery, selectedIds,
    fetch: fetchMemories, fetchStats, setTypeFilter, setSearch,
    remove, bulkRemove, toggleSelect, clearSelection,
  } = useMemoryStore();

  const [editingMemory, setEditingMemory] = useState<Memory | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [editContent, setEditContent] = useState('');

  useEffect(() => { fetchMemories(); fetchStats(); }, []);

  const filtered = memories.filter(m => {
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      return m.title.toLowerCase().includes(q) ||
        m.domain.toLowerCase().includes(q) ||
        JSON.stringify(m.content).toLowerCase().includes(q);
    }
    return true;
  });

  const handleEdit = (m: Memory) => {
    setEditingMemory(m);
    setEditTitle(m.title);
    setEditContent(JSON.stringify(m.content, null, 2));
  };

  const handleSaveEdit = async () => {
    if (!editingMemory) return;
    await updateMemory(editingMemory.id, { title: editTitle, content: editContent });
    setEditingMemory(null);
    fetchMemories();
  };

  return (
    <div className="memory-page">
      <div className="memory-header">
        <h2>Agent 记忆</h2>
        {stats && (
          <div className="memory-stats">
            <span>共 {stats.total} 条</span>
            {Object.entries(stats.by_type).map(([t, c]) => (
              <span key={t} className="stat-badge" data-type={t}>
                {TYPE_ICONS[t]} {c}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="memory-toolbar">
        <div className="memory-filters">
          <select value={typeFilter} onChange={e => setTypeFilter(e.target.value)}>
            <option value="">全部类型</option>
            <option value="site">站点经验</option>
            <option value="pattern">操作模式</option>
            <option value="failure">失败教训</option>
          </select>
          <input
            type="text" placeholder="搜索域名/标题..."
            value={searchQuery} onChange={e => setSearch(e.target.value)}
          />
        </div>
        {selectedIds.size > 0 && (
          <div className="memory-bulk-actions">
            <span>已选 {selectedIds.size} 条</span>
            <button className="btn-sm btn-danger" onClick={bulkRemove}>批量删除</button>
            <button className="btn-sm" onClick={clearSelection}>取消选择</button>
          </div>
        )}
      </div>

      {loading ? (
        <div className="memory-loading">加载中...</div>
      ) : filtered.length === 0 ? (
        <div className="memory-empty">
          暂无记忆。Agent 执行任务后会自动提取经验。
        </div>
      ) : (
        <div className="memory-grid">
          {filtered.map(m => (
            <MemoryCard
              key={m.id} memory={m}
              selected={selectedIds.has(m.id)}
              onToggle={() => toggleSelect(m.id)}
              onDelete={() => remove(m.id)}
              onEdit={handleEdit}
            />
          ))}
        </div>
      )}

      {editingMemory && (
        <div className="modal-overlay" onClick={() => setEditingMemory(null)}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <h3>编辑记忆</h3>
            <label>标题</label>
            <input type="text" value={editTitle} onChange={e => setEditTitle(e.target.value)} />
            <label>内容 (JSON)</label>
            <textarea rows={10} value={editContent} onChange={e => setEditContent(e.target.value)} />
            <div className="modal-actions">
              <button onClick={handleSaveEdit}>保存</button>
              <button onClick={() => setEditingMemory(null)}>取消</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
