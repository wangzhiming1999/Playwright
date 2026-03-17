export interface TemplateParameter {
  key: string;
  type: 'string' | 'integer' | 'float' | 'boolean' | 'json';
  description: string;
  default?: unknown;
  required: boolean;
}

export interface Template {
  id: string;
  title: string;
  description: string;
  category: string;
  tags: string[];
  icon: string;
  difficulty: 'beginner' | 'intermediate' | 'advanced';
  estimated_time: string;
  parameters: TemplateParameter[];
  yaml_source?: string;
}

export interface TemplateCategory {
  id: string;
  label: string;
  icon: string;
  count: number;
}
