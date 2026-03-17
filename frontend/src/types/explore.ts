export interface ExploreScreenshot {
  filename: string;
  url: string;
  title: string;
  score: number;
  page_type: string;
  source?: string;
}

export interface ExploreTask {
  eid: string;
  id?: string;
  url: string;
  status: 'pending' | 'running' | 'done' | 'failed';
  logs: string[];
  screenshots: (ExploreScreenshot | string)[];
  site_understanding?: SiteUnderstanding;
  visited_pages?: VisitedPage[];
  result?: any;
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

export interface VisitedPage {
  url: string;
  title: string;
  score: number;
  page_type: string;
}
