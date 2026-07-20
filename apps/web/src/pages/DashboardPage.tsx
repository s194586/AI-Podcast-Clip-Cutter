import { ArrowRight, Plus, RefreshCcw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { listProjects } from '../api/projects'
import type { Project } from '../api/types'
import { ErrorState, EmptyState, LoadingSkeleton } from '../components/StateBlocks'
import { ProgressBar } from '../components/ProgressBar'
import { StatusBadge } from '../components/StatusBadge'
import { formatDate, projectTitle, stageLabel } from '../utils/format'

const FILTERS = [
  { value: 'all', label: 'All projects' },
  { value: 'active', label: 'In progress' },
  { value: 'ready', label: 'Ready to review' },
  { value: 'attention', label: 'Needs attention' },
] as const

type ProjectFilter = (typeof FILTERS)[number]['value']

function matchesFilter(project: Project, filter: ProjectFilter): boolean {
  if (filter === 'active') {
    return project.status === 'created' || project.status === 'queued' || project.status === 'running'
  }
  if (filter === 'attention') {
    return project.status === 'failed' || project.status === 'cancelled'
  }
  return filter === 'all' || project.status === filter
}

function projectAction(project: Project): { label: string; to: string } {
  if (project.status === 'ready') {
    return { label: 'Review clips', to: `/projects/${project.id}/editor` }
  }
  if (project.status === 'queued' || project.status === 'running') {
    return { label: 'View progress', to: `/projects/${project.id}` }
  }
  if (project.status === 'created') {
    return { label: 'Start processing', to: `/projects/${project.id}` }
  }
  return { label: 'Review status', to: `/projects/${project.id}` }
}

export function DashboardPage() {
  const [projects, setProjects] = useState<Project[]>([])
  const [filter, setFilter] = useState<ProjectFilter>('all')
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
    () => projects.filter((project) => matchesFilter(project, filter)),
    [filter, projects],
  )

  const activeCount = projects.filter((project) => project.status === 'queued' || project.status === 'running').length
  const attentionCount = projects.filter((project) => project.status === 'failed' || project.status === 'cancelled').length

  return (
    <div className="space-y-6">
      <section className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 className="text-3xl font-semibold text-app-text">Projects</h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-app-muted">
            Turn a podcast into reviewable short clips, then render and export the strongest moments.
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
          <p className="app-label">In progress</p>
          <p className="mt-1 text-2xl font-semibold">{activeCount}</p>
        </div>
        <div className="app-panel-muted p-3">
          <p className="app-label">Needs attention</p>
          <p className="mt-1 text-2xl font-semibold">{attentionCount}</p>
        </div>
      </section>

      <section className="app-panel p-4">
        <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <p className="text-sm text-app-muted">Most recently updated first</p>
          <div className="flex flex-wrap gap-2" aria-label="Filter projects by status">
            {FILTERS.map((option) => (
              <button
                key={option.value}
                type="button"
                className={`rounded-md border px-3 py-2 text-sm transition ${filter === option.value ? 'border-app-accent bg-app-accent/15 text-app-text' : 'border-app-border bg-app-panelAlt text-app-muted hover:text-app-text'}`}
                aria-pressed={filter === option.value}
                onClick={() => setFilter(option.value)}
              >
                {option.label}
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
              const action = projectAction(project)
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
                    <p className="text-sm text-app-muted">YouTube source · Updated {formatDate(project.updated_at)}</p>
                  </div>
                  <div className="space-y-2">
                    <ProgressBar value={progressValue} label={stageText} tone={failed ? 'danger' : 'success'} />
                    <div className="flex justify-between text-xs text-app-muted">
                      <span>{project.clip_count ?? 0} clips</span>
                      <span>{project.accepted_clip_count ?? 0} accepted</span>
                    </div>
                  </div>
                  <Link
                    to={action.to}
                    className={`app-button justify-center ${project.status === 'ready' ? 'app-button-primary' : ''}`}
                    onClick={() => localStorage.setItem('lastProjectId', String(project.id))}
                  >
                    {action.label}
                    <ArrowRight className="h-4 w-4" aria-hidden="true" />
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
