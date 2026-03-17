import type { Task, CurationCard, CurationResult, GeneratedContent } from '@/types/task';
import type { ExploreTask } from '@/types/explore';

/** Unix timestamp (float) or ISO string → "YYYY-MM-DD HH:mm:ss" */
export function formatTimestamp(ts: number | string | undefined | null): string {
  if (ts == null || ts === '') return '';
  const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts);
  if (isNaN(d.getTime())) return String(ts);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** Normalize a raw task from backend into frontend Task shape */
export function normalizeTask(raw: any): Task {
  return {
    id: raw.id,
    task: raw.task || '',
    status: raw.status || 'pending',
    logs: raw.logs || [],
    screenshots: raw.screenshots || [],
    progress: raw.progress,
    pending_question: raw.pending_question,
    curation: raw.curation ? normalizeCurationResult(raw.curation) : undefined,
    generated: raw.generated ? normalizeGenerated(raw.generated) : undefined,
    browser_mode: raw.browser_mode,
    created_at: formatTimestamp(raw.created_at),
    started_at: formatTimestamp(raw.started_at),
    finished_at: formatTimestamp(raw.finished_at),
  };
}

/** Normalize a raw explore task from backend into frontend ExploreTask shape */
export function normalizeExploreTask(raw: any): ExploreTask {
  const eid = raw.eid || raw.id || '';
  const result = raw.result;

  // Extract site_understanding from result and normalize field names
  let site_understanding = raw.site_understanding;
  if (!site_understanding && result?.site_understanding) {
    site_understanding = result.site_understanding;
  }
  if (site_understanding) {
    site_understanding = normalizeSiteUnderstanding(site_understanding);
  }

  // Extract visited_pages from result
  const visited_pages = raw.visited_pages || result?.visited_pages || [];

  // Normalize screenshots: can be string[] or object[]
  const screenshots = (raw.screenshots || []).map((s: any) => {
    if (typeof s === 'string') return s;
    return { ...s, filename: s.filename || '' };
  });

  return {
    eid,
    url: raw.url || '',
    status: raw.status || 'pending',
    logs: raw.logs || [],
    screenshots,
    site_understanding,
    visited_pages,
    curation: raw.curation ? normalizeCurationResult(raw.curation) : undefined,
    generated: raw.generated ? normalizeGenerated(raw.generated) : undefined,
    product_context: raw.product_context,
    created_at: formatTimestamp(raw.created_at),
  };
}

function normalizeSiteUnderstanding(raw: any) {
  return {
    site_name: raw.site_name || '',
    category: raw.category || raw.site_category || '',
    login_required: raw.login_required ?? raw.needs_login ?? false,
    strategy: raw.strategy || '',
    candidate_pages: (raw.candidate_pages || raw.candidate_feature_pages || []).map((p: any) => ({
      url: p.url || p.path || '',
      title: p.title || p.label || '',
      score: p.score ?? p.marketing_score ?? 0,
      reason: p.reason || '',
    })),
  };
}

/** Normalize a single curation card */
export function normalizeCurationCard(raw: any): CurationCard {
  return {
    image: raw.image || raw.image_url || '',
    title: raw.title || '',
    summary: raw.summary || '',
    score: raw.score ?? raw.marketing_score ?? 0,
    features: raw.features || raw.feature_tags || [],
    page_type: raw.page_type || '',
  };
}

/** Normalize a full curation result */
export function normalizeCurationResult(raw: any): CurationResult {
  return {
    cards: (raw.cards || []).map(normalizeCurationCard),
    stats: raw.stats || { total: 0, after_dedup: 0, kept: 0 },
    all_results: raw.all_results || [],
  };
}

/** Normalize generated content */
export function normalizeGenerated(raw: any): GeneratedContent {
  const ai_page = raw.ai_page ? {
    hero: {
      headline: raw.ai_page.hero?.headline || '',
      subheadline: raw.ai_page.hero?.subheadline || '',
      cta: raw.ai_page.hero?.cta || raw.ai_page.hero?.cta_text || '',
    },
    features: (raw.ai_page.features || []).map((f: any) => ({
      title: f.title || '',
      description: f.description || '',
      icon: f.icon || (f.card_index != null ? String(f.card_index) : undefined),
    })),
    social_proof: raw.ai_page.social_proof || '',
    faq: (raw.ai_page.faq || []).map((f: any) => ({
      question: f.question || f.q || '',
      answer: f.answer || f.a || '',
    })),
  } : undefined;

  const tweets = raw.tweets ? {
    single_tweet: raw.tweets.single_tweet || '',
    founder_voice: raw.tweets.founder_voice || '',
    thread: raw.tweets.thread || [],
  } : undefined;

  const review = raw.review ? {
    approved: raw.review.approved ?? true,
    issues: raw.review.issues || [],
  } : undefined;

  return { ai_page, tweets, review };
}
