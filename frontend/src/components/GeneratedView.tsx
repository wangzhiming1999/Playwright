import { useState } from 'react';
import type { GeneratedContent } from '@/types/task';
import { editGenerated } from '@/api/tasks';
import { toast } from '@/utils/toast';
import './GeneratedView.css';

interface Props {
  generated: GeneratedContent;
  source: 'task' | 'explore';
  sourceId: string;
}

function EditableText({ value, field, source, sourceId }: { value: string; field: string; source: string; sourceId: string }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(value);

  async function handleSave() {
    if (text === value) { setEditing(false); return; }
    try {
      await editGenerated(source, sourceId, field, text);
      setEditing(false);
    } catch (e: any) { toast.error(`保存失败: ${e.message}`); }
  }

  if (editing) {
    return (
      <input
        className="gen-edit-input"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={handleSave}
        onKeyDown={(e) => { if (e.key === 'Enter') handleSave(); if (e.key === 'Escape') { setText(value); setEditing(false); } }}
        autoFocus
      />
    );
  }
  return <span className="gen-editable" onClick={() => setEditing(true)} title="点击编辑">{value || '(空)'}</span>;
}

export function GeneratedView({ generated, source, sourceId }: Props) {
  const { ai_page, tweets, review } = generated;

  return (
    <div className="generated-view">
      {/* Review */}
      {review && (
        <div className="gen-section">
          <div className="gen-section-title">
            审核结果
            <span className={`badge ${review.approved ? 'badge-done' : 'badge-failed'}`} style={{ marginLeft: 8 }}>
              {review.approved ? '通过' : '未通过'}
            </span>
          </div>
          {review.issues.length > 0 && (
            <ul className="gen-issues">
              {review.issues.map((issue, i) => <li key={i}>{issue}</li>)}
            </ul>
          )}
        </div>
      )}

      {/* AI Page */}
      {ai_page && (
        <div className="gen-section">
          <div className="gen-section-title">AI 落地页</div>

          <div className="gen-hero">
            <div className="gen-hero-headline">
              <EditableText value={ai_page.hero.headline} field="ai_page.hero.headline" source={source} sourceId={sourceId} />
            </div>
            <div className="gen-hero-sub">
              <EditableText value={ai_page.hero.subheadline} field="ai_page.hero.subheadline" source={source} sourceId={sourceId} />
            </div>
            <div className="gen-hero-cta">
              <EditableText value={ai_page.hero.cta} field="ai_page.hero.cta_text" source={source} sourceId={sourceId} />
            </div>
          </div>

          {ai_page.social_proof && (
            <div className="gen-social-proof">
              <EditableText value={ai_page.social_proof} field="ai_page.social_proof" source={source} sourceId={sourceId} />
            </div>
          )}

          {ai_page.features.length > 0 && (
            <div className="gen-features">
              <div className="gen-label">功能亮点</div>
              {ai_page.features.map((f, i) => (
                <div key={i} className="gen-feature-item">
                  <div className="gen-feature-title">
                    <EditableText value={f.title} field={`ai_page.features.${i}.title`} source={source} sourceId={sourceId} />
                  </div>
                  <div className="gen-feature-desc">
                    <EditableText value={f.description} field={`ai_page.features.${i}.description`} source={source} sourceId={sourceId} />
                  </div>
                </div>
              ))}
            </div>
          )}

          {ai_page.faq.length > 0 && (
            <div className="gen-faq">
              <div className="gen-label">FAQ</div>
              {ai_page.faq.map((f, i) => (
                <details key={i} className="gen-faq-item">
                  <summary>{f.question}</summary>
                  <p>{f.answer}</p>
                </details>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Tweets */}
      {tweets && (
        <div className="gen-section">
          <div className="gen-section-title">推文</div>

          <div className="gen-tweet-card">
            <div className="gen-label">单条推文</div>
            <div className="gen-tweet-text">
              <EditableText value={tweets.single_tweet} field="tweets.single_tweet" source={source} sourceId={sourceId} />
            </div>
          </div>

          <div className="gen-tweet-card">
            <div className="gen-label">创始人口吻</div>
            <div className="gen-tweet-text">
              <EditableText value={tweets.founder_voice} field="tweets.founder_voice" source={source} sourceId={sourceId} />
            </div>
          </div>

          {tweets.thread.length > 0 && (
            <div className="gen-tweet-card">
              <div className="gen-label">推文串 ({tweets.thread.length} 条)</div>
              {tweets.thread.map((t, i) => (
                <div key={i} className="gen-thread-item">
                  <span className="gen-thread-num">{i + 1}</span>
                  <EditableText value={t} field={`tweets.thread.${i}`} source={source} sourceId={sourceId} />
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {!ai_page && !tweets && (
        <div className="empty-state">暂无生成内容</div>
      )}
    </div>
  );
}
