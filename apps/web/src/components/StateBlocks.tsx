import { RefreshCcw, TriangleAlert } from 'lucide-react'

interface EmptyStateProps {
  title: string
  children?: React.ReactNode
  action?: React.ReactNode
}

interface ErrorStateProps {
  title?: string
  message: string
  onRetry?: () => void
}

export function LoadingSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3" aria-label="Loading">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="h-24 animate-pulse rounded-panel border border-app-border bg-app-panelAlt" />
      ))}
    </div>
  )
}

export function EmptyState({ title, children, action }: EmptyStateProps) {
  return (
    <div className="app-panel-muted flex min-h-56 flex-col items-center justify-center gap-4 p-8 text-center">
      <div>
        <h2 className="text-xl font-semibold text-app-text">{title}</h2>
        {children ? <div className="mt-2 max-w-xl text-sm leading-6 text-app-muted">{children}</div> : null}
      </div>
      {action}
    </div>
  )
}

export function ErrorState({ title = 'Something went wrong', message, onRetry }: ErrorStateProps) {
  return (
    <div className="rounded-panel border border-app-danger/50 bg-app-danger/10 p-5 text-red-100">
      <div className="flex items-start gap-3">
        <TriangleAlert className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <h2 className="font-semibold">{title}</h2>
          <p className="mt-1 text-sm leading-6 text-red-100/80">{message}</p>
        </div>
        {onRetry ? (
          <button type="button" className="app-button" onClick={onRetry}>
            <RefreshCcw className="h-4 w-4" aria-hidden="true" />
            Retry
          </button>
        ) : null}
      </div>
    </div>
  )
}
