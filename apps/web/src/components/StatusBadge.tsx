import { CheckCircle2, Circle, Clock3, Loader2, TriangleAlert, XCircle } from 'lucide-react'
import { statusLabel } from '../utils/format'

interface StatusBadgeProps {
  value?: string | null
  tone?: 'neutral' | 'success' | 'warning' | 'danger'
}

function toneFor(value?: string | null): NonNullable<StatusBadgeProps['tone']> {
  if (value === 'ready' || value === 'accepted' || value === 'completed' || value === 'render_ready') {
    return 'success'
  }
  if (value === 'failed' || value === 'rejected') {
    return 'danger'
  }
  if (value === 'manual_review' || value === 'completed_with_warnings' || value === 'adjust_boundaries') {
    return 'warning'
  }
  return 'neutral'
}

function Icon({ value }: { value?: string | null }) {
  if (value === 'running' || value === 'queued') {
    return <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
  }
  if (value === 'ready' || value === 'accepted' || value === 'completed' || value === 'render_ready') {
    return <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
  }
  if (value === 'failed' || value === 'rejected') {
    return <XCircle className="h-3.5 w-3.5" aria-hidden="true" />
  }
  if (value === 'manual_review' || value === 'completed_with_warnings') {
    return <TriangleAlert className="h-3.5 w-3.5" aria-hidden="true" />
  }
  if (value === 'created' || value === 'draft' || value === 'not_rendered') {
    return <Clock3 className="h-3.5 w-3.5" aria-hidden="true" />
  }
  return <Circle className="h-3.5 w-3.5" aria-hidden="true" />
}

export function StatusBadge({ value, tone }: StatusBadgeProps) {
  const resolvedTone = tone ?? toneFor(value)
  const styles = {
    neutral: 'border-app-border bg-app-panelAlt text-app-muted',
    success: 'border-app-accent/50 bg-app-accent/15 text-green-100',
    warning: 'border-app-warning/50 bg-app-warning/15 text-yellow-100',
    danger: 'border-app-danger/50 bg-app-danger/15 text-red-100',
  }
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-medium ${styles[resolvedTone]}`}>
      <Icon value={value} />
      {statusLabel(value)}
    </span>
  )
}
