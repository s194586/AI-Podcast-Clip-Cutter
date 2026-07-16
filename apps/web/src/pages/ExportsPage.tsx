import { ArrowLeft, Download, FileVideo2, RefreshCcw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useOutletContext, useParams } from 'react-router-dom'
import { apiUrl } from '../api/client'
import { getErrorMessage } from '../api/errors'
import { getProject } from '../api/projects'
import { listProjectExports } from '../api/render'
import type { ExportItem, Project } from '../api/types'
import type { AppShellContext } from '../components/AppShell'
import { ErrorState, EmptyState, LoadingSkeleton } from '../components/StateBlocks'
import { formatDate, formatFileSize, formatSeconds, projectTitle, sourceDomain } from '../utils/format'

interface ExportGroup {
  key: string
  title: string
  items: ExportItem[]
  latestCreatedAt: string | null
}

function useExportProjectId(): number | null {
  const params = useParams()
  const projectId = Number(params.projectId)
  return Number.isInteger(projectId) && projectId > 0 ? projectId : null
}

export function ExportsPage() {
  const projectId = useExportProjectId()
  const { healthError } = useOutletContext<AppShellContext>()
  const [project, setProject] = useState<Project | null>(null)
  const [exports, setExports] = useState<ExportItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadExports = useCallback(
    async (signal?: AbortSignal) => {
      if (projectId === null) {
        setError('Missing project id.')
        setLoading(false)
        return
      }
      setLoading(true)
      try {
        const [projectResponse, exportsResponse] = await Promise.all([
          getProject(projectId, signal),
          listProjectExports(projectId, signal),
        ])
        setProject(projectResponse.project)
        setExports(exportsResponse.exports)
        setError(null)
      } catch (loadError) {
        if (loadError instanceof DOMException && loadError.name === 'AbortError') {
          return
        }
        setExports([])
        setError(getErrorMessage(loadError, 'Could not load exports.'))
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
    void loadExports(controller.signal)
    return () => controller.abort()
  }, [loadExports])

  const groupedExports = useMemo(() => groupExports(exports), [exports])

  if (loading) {
    return <LoadingSkeleton rows={4} />
  }

  if (error) {
    return <ErrorState title={healthError ? 'Backend unavailable' : 'Exports unavailable'} message={error} onRetry={() => void loadExports()} />
  }

  if (!project) {
    return <ErrorState title="Exports unavailable" message="Project metadata could not be loaded." />
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <Link to={`/projects/${project.id}`} className="inline-flex items-center gap-2 text-sm text-app-muted hover:text-app-text">
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
            Back to processing
          </Link>
          <h1 className="mt-3 text-3xl font-semibold text-app-text">Exports</h1>
          <p className="mt-2 text-sm text-app-muted">
            {projectTitle(project)} - {sourceDomain(project.source_url)}
          </p>
        </div>
        <button type="button" className="app-button" onClick={() => void loadExports()}>
          <RefreshCcw className="h-4 w-4" aria-hidden="true" />
          Refresh
        </button>
      </div>

      {groupedExports.length === 0 ? (
        <EmptyState title="No rendered clips yet">
          Render a short from the editor and its safe download metadata will appear here.
        </EmptyState>
      ) : (
        <section className="grid min-w-0 gap-4 xl:grid-cols-2">
          {groupedExports.map((group) => (
            <article key={group.key} className="app-panel min-w-0 p-4">
              <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="truncate text-xl font-semibold text-app-text">{group.title}</h2>
                  <p className="mt-1 text-sm text-app-muted">Latest render {formatDate(group.latestCreatedAt)}</p>
                </div>
                <span className="inline-flex items-center gap-2 rounded-md border border-app-accent/50 bg-app-accent/15 px-2 py-1 text-xs font-medium text-green-100">
                  <FileVideo2 className="h-3.5 w-3.5" aria-hidden="true" />
                  {group.items.length} file{group.items.length === 1 ? '' : 's'}
                </span>
              </div>

              <div className="grid min-w-0 gap-3 md:grid-cols-2">
                {group.items.map((item) => (
                  <section key={item.id} className="min-w-0 rounded-panel border border-app-border bg-app-panelAlt p-3">
                    <h3 className="text-sm font-semibold text-app-text">{artifactLabel(item.artifact_type)}</h3>
                    <p className="mt-1 truncate text-xs text-app-muted">{item.filename}</p>
                    <div className="mt-3 grid min-w-0 gap-3 sm:grid-cols-[minmax(112px,160px)_1fr]">
                      <video
                        className="aspect-[9/16] max-h-72 w-full rounded-md border border-app-border bg-black object-contain"
                        src={apiUrl(item.preview_url)}
                        controls
                        preload="metadata"
                      />
                      <div className="grid min-w-0 content-between gap-3">
                        <dl className="grid gap-2 text-sm">
                          <div>
                            <dt className="app-label">Rendered</dt>
                            <dd className="mt-1 text-app-text">{formatDate(item.created_at)}</dd>
                          </div>
                          <div>
                            <dt className="app-label">Duration</dt>
                            <dd className="mt-1 text-app-text">{formatSeconds(item.duration)}</dd>
                          </div>
                          <div>
                            <dt className="app-label">File size</dt>
                            <dd className="mt-1 text-app-text">{formatFileSize(item.file_size)}</dd>
                          </div>
                        </dl>
                        <a className="app-button app-button-primary w-full" href={apiUrl(item.download_url)}>
                          <Download className="h-4 w-4" aria-hidden="true" />
                          Download
                        </a>
                      </div>
                    </div>
                  </section>
                ))}
              </div>
            </article>
          ))}
        </section>
      )}
    </div>
  )
}

function groupExports(items: ExportItem[]): ExportGroup[] {
  const groups = new Map<string, ExportGroup>()
  for (const item of items) {
    const key = item.clip_id ?? String(item.clip_database_id ?? item.id)
    const group = groups.get(key) ?? {
      key,
      title: clipExportTitle(item),
      items: [],
      latestCreatedAt: item.created_at,
    }
    group.items.push(item)
    if (new Date(item.created_at ?? 0).getTime() > new Date(group.latestCreatedAt ?? 0).getTime()) {
      group.latestCreatedAt = item.created_at
    }
    groups.set(key, group)
  }
  return [...groups.values()]
    .map((group) => ({
      ...group,
      items: group.items.sort((a, b) => artifactSortOrder(a.artifact_type) - artifactSortOrder(b.artifact_type)),
    }))
    .sort((a, b) => new Date(b.latestCreatedAt ?? 0).getTime() - new Date(a.latestCreatedAt ?? 0).getTime())
}

function clipExportTitle(item: ExportItem): string {
  if (item.clip_index !== null && item.clip_index !== undefined) {
    return `Clip ${item.clip_index}`
  }
  const match = item.clip_id?.match(/(\d+)$/)
  if (match) {
    return `Clip ${Number(match[1])}`
  }
  return item.clip_id ?? item.filename
}

function artifactLabel(value: string): string {
  if (value === 'subtitled_clip') {
    return 'With subtitles'
  }
  if (value === 'raw_clip') {
    return 'Raw'
  }
  return value.replaceAll('_', ' ')
}

function artifactSortOrder(value: string): number {
  if (value === 'subtitled_clip') {
    return 0
  }
  if (value === 'raw_clip') {
    return 1
  }
  return 2
}
