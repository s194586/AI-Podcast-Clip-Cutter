import { ArrowLeft, Ban, ExternalLink, FileVideo2, Loader2, Play, RefreshCcw, RotateCcw, TerminalSquare } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { cancelProject, getProject, getProjectLogs, getProjectStatus, startProject } from '../api/projects'
import type { Project, ProjectLogTail, ProjectStatus } from '../api/types'
import { getErrorMessage } from '../api/errors'
import { ErrorState, LoadingSkeleton } from '../components/StateBlocks'
import { ProgressBar } from '../components/ProgressBar'
import { StatusBadge } from '../components/StatusBadge'
import { PROCESSING_STAGES, formatDate, projectTitle, sourceDomain, stageLabel, shouldPollStatus } from '../utils/format'

const PROJECT_STATUS_POLL_MS = import.meta.env.MODE === 'test' ? 25 : 3000

function useProjectId(): number | null {
  const params = useParams()
  const value = Number(params.projectId)
  return Number.isInteger(value) && value > 0 ? value : null
}

export function ProjectPage() {
  const projectId = useProjectId()
  const [project, setProject] = useState<Project | null>(null)
  const [status, setStatus] = useState<ProjectStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const [action, setAction] = useState<'start' | 'cancel' | 'retry' | null>(null)
  const [logs, setLogs] = useState<ProjectLogTail | null>(null)
  const [logsLoading, setLogsLoading] = useState(false)
  const pollingInFlight = useRef(false)
  const actionInFlight = useRef(false)

  const loadProject = useCallback(
    async (signal?: AbortSignal) => {
      if (projectId === null) {
        setError('Missing project id.')
        setLoading(false)
        return
      }
      setLoading(true)
      try {
        const [projectResponse, statusResponse] = await Promise.all([
          getProject(projectId, signal),
          getProjectStatus(projectId, signal),
        ])
        setProject(projectResponse.project)
        setStatus(statusResponse)
        setError(null)
      } catch (loadError) {
        if (loadError instanceof DOMException && loadError.name === 'AbortError') {
          return
        }
        setProject(null)
        setStatus(null)
        setError(getErrorMessage(loadError, 'Could not load project.'))
      } finally {
        if (!signal?.aborted) {
          setLoading(false)
        }
      }
    },
    [projectId],
  )

  useEffect(() => {
    const controller = new AbortController()
    void loadProject(controller.signal)
    return () => controller.abort()
  }, [loadProject])

  const runStatus = status?.status ?? project?.status

  useEffect(() => {
    if (projectId === null || !shouldPollStatus(runStatus)) {
      return undefined
    }
    let active = true
    const tick = async () => {
      if (pollingInFlight.current || !active) {
        return
      }
      pollingInFlight.current = true
      const controller = new AbortController()
      try {
        const nextStatus = await getProjectStatus(projectId, controller.signal)
        if (!active) {
          return
        }
        setStatus(nextStatus)
        setProject((current) => (current ? { ...current, status: nextStatus.status, current_stage: nextStatus.current_stage, progress_percent: nextStatus.progress_percent, error_message: nextStatus.error_message ?? null, updated_at: nextStatus.updated_at } : current))
      } catch (pollError) {
        if (!(pollError instanceof DOMException && pollError.name === 'AbortError') && active) {
          setActionError(getErrorMessage(pollError, 'Could not refresh status.'))
        }
      } finally {
        pollingInFlight.current = false
      }
    }
    const interval = window.setInterval(tick, PROJECT_STATUS_POLL_MS)
    return () => {
      active = false
      window.clearInterval(interval)
    }
  }, [projectId, runStatus])

  const currentStage = status?.current_stage ?? status?.stage ?? project?.current_stage ?? project?.stage ?? 'waiting'
  const currentStageIndex = PROCESSING_STAGES.indexOf(currentStage as (typeof PROCESSING_STAGES)[number])
  const progress = status?.progress_percent ?? project?.progress_percent ?? 0
  const message = status?.message ?? stageLabel(currentStage)

  const processing = runStatus === 'queued' || runStatus === 'running'
  const canStart = runStatus === 'created' && !processing
  const canCancel = processing
  const canRetry = runStatus === 'failed' || runStatus === 'cancelled'
  const ready = runStatus === 'ready'

  const loadLogs = useCallback(async () => {
    if (projectId === null) {
      return
    }
    setLogsLoading(true)
    try {
      setLogs(await getProjectLogs(projectId, 200))
    } catch (logError) {
      setActionError(getErrorMessage(logError, 'Could not load technical details.'))
    } finally {
      setLogsLoading(false)
    }
  }, [projectId])

  async function runAction(nextAction: 'start' | 'cancel' | 'retry') {
    if (projectId === null || actionInFlight.current) {
      return
    }
    actionInFlight.current = true
    setAction(nextAction)
    setActionError(null)
    setActionMessage(null)
    try {
      const nextStatus = nextAction === 'cancel' ? await cancelProject(projectId) : (await startProject(projectId)).status
      setStatus(nextStatus)
      setProject((current) => (current ? { ...current, status: nextStatus.status, current_stage: nextStatus.current_stage, progress_percent: nextStatus.progress_percent, error_message: nextStatus.error_message ?? null } : current))
      setActionMessage(nextAction === 'cancel' ? 'Project cancelled.' : 'Processing started.')
    } catch (actionFailure) {
      setActionError(getErrorMessage(actionFailure, 'Project action failed.'))
    } finally {
      setAction(null)
      window.setTimeout(() => {
        actionInFlight.current = false
      }, 0)
    }
  }

  const pageTitle = useMemo(() => (project ? projectTitle(project) : 'Project'), [project])

  if (loading) {
    return <LoadingSkeleton rows={4} />
  }

  if (error) {
    return <ErrorState title="Project unavailable" message={error} onRetry={() => void loadProject()} />
  }

  if (!project) {
    return <ErrorState title="Project unavailable" message="The requested project could not be found." />
  }

  return (
    <div className="space-y-6">
      <Link to="/" className="inline-flex items-center gap-2 text-sm text-app-muted hover:text-app-text">
        <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        Back to projects
      </Link>

      <section className="grid gap-4 lg:grid-cols-[1fr_420px]">
        <div className="app-panel p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h1 className="truncate text-3xl font-semibold text-app-text">{pageTitle}</h1>
              <p className="mt-2 text-sm text-app-muted">{sourceDomain(project.source_url)}</p>
            </div>
            <StatusBadge value={runStatus} />
          </div>
          <div className="mt-6 space-y-4">
            <ProgressBar value={progress} label={message} />
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="app-panel-muted p-3">
                <p className="app-label">Clips</p>
                <p className="mt-1 text-xl font-semibold">{status?.clip_count ?? project.clip_count ?? 0}</p>
              </div>
              <div className="app-panel-muted p-3">
                <p className="app-label">Updated</p>
                <p className="mt-1 text-sm text-app-text">{formatDate(status?.updated_at ?? project.updated_at)}</p>
              </div>
              <div className="app-panel-muted p-3">
                <p className="app-label">AI review</p>
                <p className="mt-1 text-sm text-app-text">{project.auto_review ? 'Automatic' : 'Manual trigger'}</p>
              </div>
            </div>
            {project.error_message || status?.error_message || status?.last_error ? (
              <p className="rounded-md border border-app-danger/50 bg-app-danger/10 p-3 text-sm text-red-100">{project.error_message ?? status?.error_message ?? status?.last_error}</p>
            ) : null}
          </div>
        </div>

        <div className="app-panel p-5">
          <h2 className="app-section-title">Actions</h2>
          <div className="mt-4 grid gap-2">
            <button type="button" className="app-button app-button-primary" disabled={!canStart || Boolean(action)} onClick={() => void runAction('start')}>
              {action === 'start' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Play className="h-4 w-4" aria-hidden="true" />}
              Start Processing
            </button>
            <button type="button" className="app-button" disabled={!canRetry || Boolean(action)} onClick={() => void runAction('retry')}>
              {action === 'retry' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <RotateCcw className="h-4 w-4" aria-hidden="true" />}
              Retry
            </button>
            <button type="button" className="app-button app-button-danger" disabled={!canCancel || Boolean(action)} onClick={() => void runAction('cancel')}>
              {action === 'cancel' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Ban className="h-4 w-4" aria-hidden="true" />}
              Cancel
            </button>
            <div className="grid gap-2 sm:grid-cols-2">
              <Link to={`/projects/${project.id}/editor`} className={`app-button ${ready ? '' : 'opacity-70'}`}>
                <ExternalLink className="h-4 w-4" aria-hidden="true" />
                Open Editor
              </Link>
              <Link to={`/projects/${project.id}/exports`} className="app-button">
                <FileVideo2 className="h-4 w-4" aria-hidden="true" />
                Open Exports
              </Link>
            </div>
          </div>
          {actionMessage ? <p className="mt-3 rounded-md border border-app-accent/50 bg-app-accent/10 p-3 text-sm text-green-100">{actionMessage}</p> : null}
          {actionError ? <p className="mt-3 rounded-md border border-app-danger/50 bg-app-danger/10 p-3 text-sm text-red-100">{actionError}</p> : null}
        </div>
      </section>

      <section className="app-panel p-5">
        <h2 className="app-section-title">Processing stages</h2>
        <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {PROCESSING_STAGES.map((stage, index) => {
            const completed = runStatus === 'ready' || (currentStageIndex >= 0 && index < currentStageIndex)
            const current = stage === currentStage && runStatus !== 'ready'
            return (
              <div key={stage} className={`rounded-panel border p-4 ${completed ? 'border-app-accent/50 bg-app-accent/10' : current ? 'border-app-accent bg-app-accent/15' : 'border-app-border bg-app-panelAlt text-app-muted'}`}>
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-medium">{stageLabel(stage)}</p>
                  <StatusBadge value={completed ? 'ready' : current ? runStatus : 'created'} tone={completed ? 'success' : current ? 'neutral' : 'neutral'} />
                </div>
              </div>
            )
          })}
        </div>
      </section>

      <details className="app-panel p-5" onToggle={(event) => { if ((event.currentTarget as HTMLDetailsElement).open && logs === null) void loadLogs() }}>
        <summary className="flex cursor-pointer items-center gap-2 text-sm font-semibold text-app-text">
          <TerminalSquare className="h-4 w-4 text-app-accent" aria-hidden="true" />
          Technical details
        </summary>
        <div className="mt-4">
          {status?.orchestrator_type === 'airflow' ? (
            <div className="mb-4 border-b border-app-border pb-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h3 className="text-sm font-semibold text-app-text">Airflow run</h3>
                {status.airflow_ui_url ? (
                  <a className="app-button" href={status.airflow_ui_url} target="_blank" rel="noopener noreferrer">
                    <ExternalLink className="h-4 w-4" aria-hidden="true" />
                    Open in Airflow
                  </a>
                ) : null}
              </div>
              <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
                <div><dt className="app-label">State</dt><dd className="mt-1 text-app-text">{status.airflow_state ?? 'Unavailable'}</dd></div>
                <div><dt className="app-label">Current task</dt><dd className="mt-1 break-words text-app-text">{status.airflow_task_id ?? 'Waiting'}</dd></div>
                <div><dt className="app-label">Attempt</dt><dd className="mt-1 text-app-text">{status.retry_attempt ?? 0} of {status.retry_max_attempts ?? 0}</dd></div>
                <div><dt className="app-label">Run ID</dt><dd className="mt-1 break-all text-app-muted">{status.airflow_dag_run_id ?? 'Pending'}</dd></div>
              </dl>
            </div>
          ) : null}
          <button type="button" className="app-button" onClick={() => void loadLogs()} disabled={logsLoading}>
            {logsLoading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <RefreshCcw className="h-4 w-4" aria-hidden="true" />}
            Refresh log tail
          </button>
          <pre className="mt-3 max-h-80 overflow-auto rounded-panel border border-app-border bg-black/40 p-4 text-xs leading-5 text-app-muted">
            {logsLoading ? 'Loading...' : logs?.lines.length ? logs.lines.join('\n') : 'No technical log lines available.'}
          </pre>
        </div>
      </details>
    </div>
  )
}
