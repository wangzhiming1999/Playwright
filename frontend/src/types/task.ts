export type TaskStatus = 'pending' | 'running' | 'done' | 'failed' | 'waiting_input' | 'cancelled';

export interface Task {
  id: string;
  task: string;
  status: TaskStatus;
  logs: string[];
  screenshots: string[];
  progress?: { current: number; total: number };
  pending_question?: { question: string; reason: string };
  curation?: CurationResult;
  generated?: GeneratedContent;
  browser_mode?: string;
  created_at?: string;
}

export interface CurationCard {
  image: string;
  title: string;
  summary: string;
  score: number;
  features: string[];
  page_type: string;
}

export interface CurationResult {
  cards: CurationCard[];
  stats: { total: number; after_dedup: number; kept: number };
  all_results: unknown[];
}

export interface GeneratedContent {
  ai_page?: {
    hero: { headline: string; subheadline: string; cta: string };
    features: { title: string; description: string; icon?: string }[];
    faq: { question: string; answer: string }[];
  };
  tweets?: {
    single_tweet: string;
    founder_voice: string;
    thread: string[];
  };
  review?: {
    approved: boolean;
    issues: string[];
  };
}
