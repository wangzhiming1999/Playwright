export type TaskStatus = 'pending' | 'running' | 'done' | 'failed' | 'waiting_input' | 'cancelled';

export interface StepTrace {
  step: number;
  input_mode: 'screenshot' | 'dom' | 'unknown';
  elements_count: number;
  page_url: string;
  page_title: string;
  tool_name: string;
  tool_args: Record<string, unknown>;
  is_multi_action: boolean;
  action_count: number;
  result: string;
  result_is_error: boolean;
  duration_ms: number;
  verify_changed: boolean;
  verify_type: string;
  verify_nudge: string;
  url_before: string;
  url_after: string;
  page_changed: boolean;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  cost_usd: number;
  model: string;
  nudges: string[];
  events: string[];
}

export interface TaskTrace {
  task_id: string;
  task: string;
  started_at: string;
  finished_at: string;
  success: boolean;
  reason: string;
  total_steps: number;
  total_cost_usd: number;
  steps: StepTrace[];
}

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
  started_at?: string;
  finished_at?: string;
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
    social_proof?: string;
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
