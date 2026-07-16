import { formatPercent } from '../utils/format'

interface ProgressBarProps {
  value?: number | null
  label?: string
  tone?: 'success' | 'danger' | 'neutral'
}

export function ProgressBar({ value, label, tone = 'success' }: ProgressBarProps) {
  const percent = Math.max(0, Math.min(Number(value ?? 0), 100))
  const fillClass = {
    success: 'bg-app-accent',
    danger: 'bg-app-danger',
    neutral: 'bg-app-muted',
  }[tone]
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3 text-xs text-app-muted">
        <span>{label ?? 'Progress'}</span>
        <span>{formatPercent(percent)}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-black/35" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
        <div className={`h-full rounded-full transition-all ${fillClass}`} style={{ width: `${percent}%` }} />
      </div>
    </div>
  )
}
