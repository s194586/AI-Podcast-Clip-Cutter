import { apiRequest } from './client'
import type { ProjectReviewResult, ReviewResult } from './types'

export function reviewProjectClip(projectId: number, clipId: string, signal?: AbortSignal): Promise<ReviewResult> {
  return apiRequest<ReviewResult>(`/projects/${projectId}/clips/${encodeURIComponent(clipId)}/review`, {
    method: 'POST',
    signal,
  })
}

export function reviewProjectClips(projectId: number, signal?: AbortSignal): Promise<ProjectReviewResult> {
  return apiRequest<ProjectReviewResult>(`/projects/${projectId}/review-clips`, {
    method: 'POST',
    json: { apply_safe_suggestions: true },
    signal,
  })
}
