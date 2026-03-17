import { api } from './client';
import type { Workflow, WorkflowRun } from '@/types/workflow';

export function listWorkflows() {
  return api.get<Workflow[]>('/workflows');
}

export function getWorkflow(id: string) {
  return api.get<Workflow>(`/workflows/${id}`);
}

export function createWorkflow(yamlContent: string) {
  return api.post<Workflow>('/workflows', { yaml_content: yamlContent });
}

export function updateWorkflow(id: string, yamlContent: string) {
  return api.put<Workflow>(`/workflows/${id}`, { yaml_content: yamlContent });
}

export function deleteWorkflow(id: string) {
  return api.del<{ status: string }>(`/workflows/${id}`);
}

export function runWorkflow(id: string, parameters?: Record<string, unknown>) {
  return api.post<{ run_id: string }>(`/workflows/${id}/run`, { parameters });
}

export function listWorkflowRuns(workflowId: string) {
  return api.get<WorkflowRun[]>(`/workflows/${workflowId}/runs`);
}

export function getWorkflowRun(runId: string) {
  return api.get<WorkflowRun>(`/workflow-runs/${runId}`);
}
