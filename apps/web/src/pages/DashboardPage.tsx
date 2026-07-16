import { ArrowDownWideNarrow, ExternalLink, Plus, RefreshCcw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { listProjects } from '../api/projects'
import type { Project } from '../api/types'
import { ErrorState, EmptyState, LoadingSkeleton } from '../components/StateBlocks'
import { ProgressBar } from '../components/ProgressBar'
import { StatusBadge } from '../components/StatusBadge'
import { formatDate, projectTitle, sourceDomain, stageLabel } from '../utils/format'

const FILTERS = ['all', 'created', 'queued', 'running', 'ready', 'failed', 'cancelled'] as const

export function DashboardPage() {
  const [projects, setProjects] = useState<Project[]>([])
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

  const refresh = useCallback(() => setRefreshKey((value) => value + 1), [])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    listProjects(controller.signal)
      .then((response) => {
        const sorted = [...response.projects].sort((a, b) => {
          const left = new Date(a.updated_at ?? a.created_at ?? 0).getTime()
          const right = new Date(b.updated_at ?? b.created_at ?? 0).getTime()
          return right - left
        })
        setProjects(sorted)
        setError(null)
      })
      .catch((fetchError: unknown) => {
        if (fetchError instanceof DOMException && fetchError.name === 'AbortError') {
          return
        }
        setProjects([])
        setError(fetchError instanceof Error ? fetchError.message : 'Could not load projects.')
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false)
        }
      })
    return () => controller.abort()
  }, [refreshKey])

  const visibleProjects = useMemo(
    () => projects.filter((project) => filter === 'all' || project.status === filter),
    [filter, projects],
  )

  return (
    <div className="space-y-6">
      <section className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 className="text-3xl font-semibold text-app-text">Projects</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-app-muted">
            Create a podcast project, track deterministic processing, review candidate shorts, and render final exports from one workspace.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="app-button" onClick={refresh} disabled={loading}>
            <RefreshCcw className="h-4 w-4" aria-hidden="true" />
            Refresh
          </button>
          <Link to="/projects/new" className="app-button app-button-primary">
            <Plus className="h-4 w-4" aria-hidden="true" />
            New Project
          </Link>
        </div>
      </section>

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="app-panel-muted p-3">
          <p className="app-label">Total projects</p>
          <p className="mt-1 text-2xl font-semibold">{projects.length}</p>
        </div>
        <div className="app-panel-muted p-3">
          <p className="app-label">Ready</p>
          <p className="mt-1 text-2xl font-semibold">{projects.filter((project) => project.status === 'ready').length}</p>
        </div>
        <div className="app-panel-muted p-3">
          <p className="app-label">Running</p>
          <p className="mt-1 text-2xl font-semibold">{projects.filter((project) => project.status === 'running' || project.status === 'queued').length}</p>
        </div>
        <div className="app-panel-muted p-3">
          <p className="app-label">Accepted clips</p>
          <p className="mt-1 text-2xl font-semibold">{projects.reduce((total, project) => total + (project.accepted_clip_count ?? 0), 0)}</p>
        </div>
      </section>

      <section className="app-panel p-4">
        <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-2 text-sm font-medium text-app-muted">
            <ArrowDownWideNarrow className="h-4 w-4" aria-hidden="true" />
            Sorted by updated time
          </div>
          <div className="flex flex-wrap gap-2" aria-label="Filter projects by status">
            {FILTERS.map((value) => (
              <button
                key={value}
                type="button"
                className={`rounded-md border px-3 py-2 text-sm transition ${filter === value ? 'border-app-accent bg-app-accent/15 text-app-text' : 'border-app-border bg-app-panelAlt text-app-muted hover:text-app-text'}`}
                onClick={() => setFilter(value)}
              >
                {value === 'all' ? 'All' : value.replaceAll('_', ' ')}
              </button>
            ))}
          </div>
        </div>

        {loading ? <LoadingSkeleton rows={4} /> : null}
        {!loading && error ? <ErrorState title="Dashboard API error" message={error} onRetry={refresh} /> : null}
        {!loading && !error && projects.length === 0 ? (
          <EmptyState title="No projects yet" action={<Link to="/projects/new" className="app-button app-button-primary"><Plus className="h-4 w-4" aria-hidden="true" />New Project</Link>}>
            Start with a YouTube URL and the backend will create an isolated project workspace.
          </EmptyState>
        ) : null}
        {!loading && !error && projects.length > 0 && visibleProjects.length === 0 ? (
          <EmptyState title="No projects match this filter">Try a different status filter or refresh the dashboard.</EmptyState>
        ) : null}

        {!loading && !error && visibleProjects.length > 0 ? (
          <div className="grid gap-3">
            {visibleProjects.map((project) => {
              const failed = project.status === 'failed'
              const progressValue = failed ? Math.min(project.progress_percent ?? 0, 95) : project.progress_percent
              const stageText = failed && project.error_message
                ? `${stageLabel(project.current_stage ?? project.stage)}: ${project.error_message}`
                : stageLabel(project.current_stage ?? project.stage)
              return (
              <article key={project.id} className={`rounded-panel border p-4 transition ${failed ? 'border-app-danger/60 bg-app-danger/10' : 'border-app-border bg-app-panelAlt hover:border-app-muted'}`}>
                <div className="grid gap-4 lg:grid-cols-[1fr_220px_160px] lg:items-center">
                  <div className="min-w-0">
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      <h2 className="truncate text-lg font-semibold text-app-text">{projectTitle(project)}</h2>
                      <StatusBadge value={project.status} />
                    </div>
                    <p className="truncate text-sm text-app-muted">{sourceDomain(project.source_url)}</p>
                    <p className="mt-1 text-sm text-app-faint">Updated {formatDate(project.updated_at)}</p>
                  </div>
                  <div className="space-y-2">
                    <ProgressBar value={progressValue} label={stageText} tone={failed ? 'danger' : 'success'} />
                    <div className="flex justify-between text-xs text-app-muted">
                      <span>{project.clip_count ?? 0} clips</span>
                      <span>{project.accepted_clip_count ?? 0} accepted</span>
                    </div>
                  </div>
                  <Link
                    to={`/projects/${project.id}`}
                    className="app-button justify-center"
                    onClick={() => localStorage.setItem('lastProjectId', String(project.id))}
                  >
                    <ExternalLink className="h-4 w-4" aria-hidden="true" />
                    Open Project
                  </Link>
                </div>
              </article>
            )})}
          </div>
        ) : null}
      </section>
    </div>
  )
}
