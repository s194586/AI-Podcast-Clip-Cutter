import { apiRequest } from './client'
import type { ClipResponse, ClipsResponse } from './types'

export function listProjectClips(projectId: number, signal?: AbortSignal): Promise<ClipsResponse> {
  return apiRequest<ClipsResponse>(`/projects/${projectId}/clips`, { signal })
}

export function updateProjectClipBounds(
  projectId: number,
  clipId: string,
  start: number,
  end: number,
  signal?: AbortSignal,
): Promise<ClipResponse> {
  return apiRequest<ClipResponse>(`/projects/${projectId}/clips/${encodeURIComponent(clipId)}`, {
    method: 'PATCH',
    json: { start, end },
    signal,
  })
}

export function acceptProjectClip(projectId: number, clipId: string, signal?: AbortSignal): Promise<ClipResponse> {
  return apiRequest<ClipResponse>(`/projects/${projectId}/clips/${encodeURIComponent(clipId)}/accept`, {
    method: 'POST',
    signal,
  })
}

export function rejectProjectClip(projectId: number, clipId: string, signal?: AbortSignal): Promise<ClipResponse> {
  return apiRequest<ClipResponse>(`/projects/${projectId}/clips/${encodeURIComponent(clipId)}/reject`, {
    method: 'POST',
    signal,
  })
}
