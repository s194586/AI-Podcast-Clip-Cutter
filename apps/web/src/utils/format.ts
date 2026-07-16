import type { Clip, HealthStatus, Project, StageName } from '../api/types'

export const PROCESSING_STAGES = [
  'waiting',
  'downloading',
  'transcribing',
  'validating_transcript',
  'generating_candidates',
  'importing_candidates',
  'reviewing_with_ai',
  'ready',
] as const

const STAGE_LABELS: Record<string, string> = {
  waiting: 'Waiting',
  downloading: 'Downloading',
  transcribing: 'Transcribing',
  validating_transcript: 'Validating transcript',
  generating_candidates: 'Generating candidates',
  importing_candidates: 'Importing candidates',
  reviewing_with_ai: 'AI review',
  ready: 'Ready',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

const STATUS_LABELS: Record<string, string> = {
  created: 'Created',
  queued: 'Queued',
  running: 'Running',
  ready: 'Ready',
  failed: 'Failed',
  cancelled: 'Cancelled',
  draft: 'Draft',
  accepted: 'Accepted',
  rejected: 'Rejected',
  not_rendered: 'Not rendered',
  completed: 'Rendered',
  completed_with_warnings: 'Rendered with warnings',
  render_ready: 'Ready',
  adjust_boundaries: 'Adjusted',
  manual_review: 'Manual review',
  local_stub: 'Local stub',
  gemini: 'Gemini',
}

export function statusLabel(value: string | null | undefined): string {
  if (!value) {
    return 'Unknown'
  }
  return STATUS_LABELS[value] ?? value.replaceAll('_', ' ')
}

export function stageLabel(stage: StageName | null | undefined): string {
  if (!stage) {
    return 'Waiting'
  }
  return STAGE_LABELS[stage] ?? statusLabel(stage)
}

export function projectTitle(project: Pick<Project, 'id' | 'title'>): string {
  return project.title?.trim() || `Project ${project.id}`
}

export function clipTitle(clip: Pick<Clip, 'index' | 'id'>): string {
  return `Clip ${clip.index || clip.id}`
}

export function formatPercent(value: number | null | undefined): string {
  const percent = Number.isFinite(value) ? Math.round(Number(value)) : 0
  return `${Math.max(0, Math.min(percent, 100))}%`
}

export function formatSeconds(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return '-'
  }
  const seconds = Number(value)
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds - minutes * 60
  if (minutes > 0) {
    return `${minutes}:${remainder.toFixed(1).padStart(4, '0')}`
  }
  return `${remainder.toFixed(1)}s`
}

export function formatDate(value: string | null | undefined): string {
  if (!value) {
    return 'Not yet'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date)
}

export function sourceDomain(sourceUrl: string): string {
  try {
    return new URL(sourceUrl).hostname.replace(/^www\./, '')
  } catch {
    return sourceUrl || 'Unknown source'
  }
}

export function formatFileSize(bytes: number | null | undefined): string {
  if (!bytes || bytes <= 0) {
    return '-'
  }
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`
}

export function reviewerLabel(health: HealthStatus | null): string {
  if (!health) {
    return 'Reviewer unknown'
  }
  const provider = health.clip_review_provider ?? health.review_config?.provider
  const model = health.clip_review_model ?? health.review_config?.model
  if (!provider) {
    return 'Reviewer not configured'
  }
  if (model) {
    return `${statusLabel(provider)} / ${model}`
  }
  return statusLabel(provider)
}

export function reviewerReadyLabel(health: HealthStatus | null): string {
  const provider = health?.clip_review_provider ?? health?.review_config?.provider
  if (provider === 'gemini') {
    return health?.gemini_api_key_configured ? 'Gemini configured' : 'Gemini key missing'
  }
  if (provider) {
    return `${statusLabel(provider)} configured`
  }
  return 'Reviewer unknown'
}

export function isTerminalStatus(status: string | null | undefined): boolean {
  return status === 'ready' || status === 'failed' || status === 'cancelled'
}

export function shouldPollStatus(status: string | null | undefined): boolean {
  return status === 'created' || status === 'queued' || status === 'running'
}
