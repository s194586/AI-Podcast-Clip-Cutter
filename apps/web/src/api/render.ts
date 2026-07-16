import { apiRequest } from './client'
import type { ExportsResponse, RenderResult } from './types'

export function renderProjectClip(
  projectId: number,
  clipId: string,
  start: number,
  end: number,
  signal?: AbortSignal,
): Promise<RenderResult> {
  return apiRequest<RenderResult>(`/projects/${projectId}/render`, {
    method: 'POST',
    json: { clip_id: clipId, start, end },
    signal,
  })
}

export function listProjectExports(projectId: number, signal?: AbortSignal): Promise<ExportsResponse> {
  return apiRequest<ExportsResponse>(`/projects/${projectId}/exports`, { signal })
}
