export interface ExploreTask {
  eid: string;
  url: string;
  status: 'pending' | 'running' | 'done' | 'failed';
  logs: string[];
  screenshots: string[];
  site_understanding?: SiteUnderstanding;
  curation?: import('./task').CurationResult;
  generated?: import('./task').GeneratedContent;
  product_context?: string;
  created_at?: string;
}

export interface SiteUnderstanding {
  site_name: string;
  category: string;
  login_required: boolean;
  strategy: string;
  candidate_pages: CandidatePage[];
}

export interface CandidatePage {
  url: string;
  title: string;
  score: number;
  reason: string;
}
