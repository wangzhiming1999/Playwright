import { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { listTemplates, listCategories, getTemplate, runTemplate, instantiateTemplate } from '@/api/templates';
import { toast } from '@/utils/toast';
import type { Template, TemplateCategory, TemplateParameter } from '@/types/template';
import './TemplatesPage.css';

const DIFFICULTY_LABEL: Record<string, string> = { beginner: '入门', intermediate: '中级', advanced: '高级' };
const DIFFICULTY_CLASS: Record<string, string> = { beginner: 'badge-done', intermediate: 'badge-running', advanced: 'badge-failed' };

export function TemplatesPage() {
  const navigate = useNavigate();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [categories, setCategories] = useState<TemplateCategory[]>([]);
  const [activeCategory, setActiveCategory] = useState<string>('');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<(Template & { yaml_source?: string }) | null>(null);
  const [paramValues, setParamValues] = useState<Record<string, unknown>>({});
  const [running, setRunning] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    listTemplates().then(setTemplates).catch(() => toast.error('加载模板失败'));
    listCategories().then(setCategories).catch(() => {});
  }, []);

  const filtered = useMemo(() => {
    let list = templates;
    if (activeCategory) list = list.filter((t) => t.category === activeCategory);
    if (search) {
      const q = search.toLowerCase();
      list = list.filter((t) => t.title.toLowerCase().includes(q) || t.description.toLowerCase().includes(q) || t.tags.some((tag) => tag.includes(q)));
    }
    return list;
  }, [templates, activeCategory, search]);

  async function handleSelect(tpl: Template) {
    try {
      const full = await getTemplate(tpl.id);
      setSelected(full);
      const defaults: Record<string, unknown> = {};
      for (const p of full.parameters) {
        if (p.default != null) defaults[p.key] = p.default;
      }
      setParamValues(defaults);
    } catch { setSelected(tpl); setParamValues({}); }
  }

  async function handleRun() {
    if (!selected) return;
    setRunning(true);
    try {
      const res = await runTemplate(selected.id, paramValues);
      toast.success('模板已开始运行');
      navigate(`/workflows/${res.workflow_id}`);
    } catch (e) { toast.error(`运行失败: ${e instanceof Error ? e.message : String(e)}`); }
    setRunning(false);
  }

  async function handleInstantiate() {
    if (!selected) return;
    setSaving(true);
    try {
      const res = await instantiateTemplate(selected.id);
      toast.success(`已保存为工作流: ${res.title}`);
      navigate(`/workflows/${res.id}`);
    } catch (e) { toast.error(`保存失败: ${e instanceof Error ? e.message : String(e)}`); }
    setSaving(false);
  }

  function setParam(key: string, value: unknown) {
    setParamValues((prev) => ({ ...prev, [key]: value }));
  }

  return (
    <div className="templates-page">
      <header className="templates-header">
        <h1>模板市场</h1>
        <input className="templates-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="搜索模板..." />
      </header>

      <div className="templates-categories">
        <button className={`chip ${activeCategory === '' ? 'active' : ''}`} onClick={() => setActiveCategory('')}>
          全部 ({templates.length})
        </button>
        {categories.map((cat) => (
          <button key={cat.id} className={`chip ${activeCategory === cat.id ? 'active' : ''}`} onClick={() => setActiveCategory(cat.id)}>
            {cat.label} ({cat.count})
          </button>
        ))}
      </div>

      <div className="templates-body">
        <div className="templates-grid">
          {filtered.length === 0 ? (
            <div className="empty-state">暂无匹配的模板</div>
          ) : filtered.map((tpl) => (
            <div key={tpl.id} className={`template-card ${selected?.id === tpl.id ? 'active' : ''}`} onClick={() => handleSelect(tpl)}>
              <div className="template-card-header">
                <span className="template-card-title">{tpl.title}</span>
                <span className={`badge ${DIFFICULTY_CLASS[tpl.difficulty] || ''}`}>{DIFFICULTY_LABEL[tpl.difficulty] || tpl.difficulty}</span>
              </div>
              <div className="template-card-desc">{tpl.description}</div>
              <div className="template-card-footer">
                <span className="template-card-time">{tpl.estimated_time}</span>
                <div className="template-card-tags">
                  {tpl.tags.slice(0, 3).map((tag) => <span key={tag} className="template-tag">{tag}</span>)}
                </div>
              </div>
            </div>
          ))}
        </div>

        {selected && (
          <div className="template-detail">
            <div className="template-detail-header">
              <h2>{selected.title}</h2>
              <button className="btn-ghost" onClick={() => setSelected(null)} style={{ padding: '4px 8px' }}>×</button>
            </div>
            <p className="template-detail-desc">{selected.description}</p>

            <div className="template-detail-meta">
              <span className={`badge ${DIFFICULTY_CLASS[selected.difficulty]}`}>{DIFFICULTY_LABEL[selected.difficulty]}</span>
              <span className="template-detail-time">{selected.estimated_time}</span>
            </div>

            {selected.parameters.length > 0 && (
              <div className="template-params">
                <h3>参数配置</h3>
                {selected.parameters.map((p) => (
                  <ParameterInput key={p.key} param={p} value={paramValues[p.key]} onChange={(v) => setParam(p.key, v)} />
                ))}
              </div>
            )}

            {selected.yaml_source && (
              <details className="template-yaml-details">
                <summary>查看 YAML 源码</summary>
                <pre className="template-yaml">{selected.yaml_source}</pre>
              </details>
            )}

            <div className="template-actions">
              <button className="btn-primary" onClick={handleRun} disabled={running}>
                {running ? '运行中...' : '一键运行'}
              </button>
              <button className="btn-ghost" onClick={handleInstantiate} disabled={saving}>
                {saving ? '保存中...' : '保存为工作流'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function ParameterInput({ param, value, onChange }: { param: TemplateParameter; value: unknown; onChange: (v: unknown) => void }) {
  const id = `param-${param.key}`;
  return (
    <div className="param-field">
      <label htmlFor={id}>
        {param.description || param.key}
        {param.required && <span className="param-required">*</span>}
      </label>
      {param.type === 'boolean' ? (
        <label className="param-checkbox">
          <input id={id} type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />
          {param.key}
        </label>
      ) : param.type === 'json' ? (
        <textarea id={id} value={typeof value === 'string' ? value : JSON.stringify(value || '', null, 2)} onChange={(e) => onChange(e.target.value)} placeholder={param.default != null ? String(param.default) : ''} rows={3} />
      ) : param.type === 'integer' || param.type === 'float' ? (
        <input id={id} type="number" step={param.type === 'float' ? '0.01' : '1'} value={value != null ? String(value) : ''} onChange={(e) => onChange(param.type === 'integer' ? parseInt(e.target.value) || '' : parseFloat(e.target.value) || '')} placeholder={param.default != null ? String(param.default) : ''} />
      ) : (
        <input id={id} type="text" value={value != null ? String(value) : ''} onChange={(e) => onChange(e.target.value)} placeholder={param.default != null ? String(param.default) : ''} />
      )}
    </div>
  );
}
