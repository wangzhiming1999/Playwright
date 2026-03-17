export interface Workflow {
  id: string;
  title: string;
  description?: string;
  yaml_content: string;
  created_at: string;
  updated_at: string;
}

export interface WorkflowRun {
  run_id: string;
  workflow_id: string;
  status: 'pending' | 'running' | 'done' | 'failed';
  parameters: Record<string, unknown>;
  result?: unknown;
  logs: string[];
  started_at: string;
  finished_at?: string;
}

export interface WorkflowParameter {
  key: string;
  type: 'string' | 'number' | 'boolean' | 'url';
  description?: string;
  default?: unknown;
  required?: boolean;
}
