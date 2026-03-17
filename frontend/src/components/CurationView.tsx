import type { CurationResult } from '@/types/task';
import './CurationView.css';

interface Props {
  curation: CurationResult;
  screenshotPrefix: string;
}

export function CurationView({ curation, screenshotPrefix }: Props) {
  const { cards, stats } = curation;

  return (
    <div className="curation-view">
      <div className="curation-stats">
        <span>总截图 <b>{stats.total}</b></span>
        <span className="curation-stats-arrow">&rarr;</span>
        <span>去重后 <b>{stats.after_dedup}</b></span>
        <span className="curation-stats-arrow">&rarr;</span>
        <span>保留 <b>{stats.kept}</b></span>
      </div>

      {cards.length === 0 ? (
        <div className="empty-state">没有符合条件的截图</div>
      ) : (
        <div className="curation-grid">
          {cards.map((card, i) => (
            <div key={i} className="curation-card">
              {card.image && (
                <img src={card.image.startsWith('/') ? card.image : `${screenshotPrefix}/${card.image}`} alt={card.title} loading="lazy" />
              )}
              <div className="curation-card-body">
                <div className="curation-card-header">
                  <span className="curation-card-title">{card.title}</span>
                  <span className={`curation-score ${card.score >= 7 ? 'score-high' : card.score >= 5 ? 'score-mid' : 'score-low'}`}>
                    {card.score.toFixed(1)}
                  </span>
                </div>
                <div className="curation-card-summary">{card.summary}</div>
                {card.features.length > 0 && (
                  <div className="curation-tags">
                    {card.features.map((f, j) => <span key={j} className="curation-tag">{f}</span>)}
                  </div>
                )}
                {card.page_type && <div className="curation-page-type">{card.page_type}</div>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
