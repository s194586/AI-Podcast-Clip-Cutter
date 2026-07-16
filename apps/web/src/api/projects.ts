import { apiRequest } from './client'
import type {
  CreateProjectPayload,
  CreateProjectResponse,
  ProjectLogTail,
  ProjectResponse,
  ProjectStatus,
  ProjectsResponse,
} from './types'

export function listProjects(signal?: AbortSignal): Promise<ProjectsResponse> {
  return apiRequest<ProjectsResponse>('/projects', { signal })
}

export function getProject(projectId: number, signal?: AbortSignal): Promise<ProjectResponse> {
  return apiRequest<ProjectResponse>(`/projects/${projectId}`, { signal })
}

export function createProject(payload: CreateProjectPayload, signal?: AbortSignal): Promise<CreateProjectResponse> {
  return apiRequest<CreateProjectResponse>('/projects', {
    method: 'POST',
    json: payload,
    signal,
  })
}

export function startProject(projectId: number, signal?: AbortSignal): Promise<{ status: ProjectStatus }> {
  return apiRequest<{ status: ProjectStatus }>(`/projects/${projectId}/start`, {
    method: 'POST',
    signal,
  })
}

export function cancelProject(projectId: number, signal?: AbortSignal): Promise<ProjectStatus> {
  return apiRequest<ProjectStatus>(`/projects/${projectId}/cancel`, {
    method: 'POST',
    signal,
  })
}

export function getProjectStatus(projectId: number, signal?: AbortSignal): Promise<ProjectStatus> {
  return apiRequest<ProjectStatus>(`/projects/${projectId}/status`, { signal })
}

export function getProjectLogs(projectId: number, tail = 200, signal?: AbortSignal): Promise<ProjectLogTail> {
  return apiRequest<ProjectLogTail>(`/projects/${projectId}/logs?tail=${tail}`, { signal })
}
