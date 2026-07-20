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
import { formatDate, formatFileSize, formatSeconds, projectTitle } from '../utils/format'

interface ExportGroup {
  key: string
  title: string
  attempts: ExportAttempt[]
  latestCreatedAt: string | null
}

interface ExportAttempt {
  key: string
  items: ExportItem[]
  createdAt: string | null
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
          <Link to={`/projects/${project.id}/editor`} className="inline-flex items-center gap-2 text-sm text-app-muted hover:text-app-text">
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
            Back to editor
          </Link>
          <h1 className="mt-3 text-3xl font-semibold text-app-text">Exports</h1>
          <p className="mt-2 text-sm text-app-muted">
            Download the latest finished version of each clip from {projectTitle(project)}.
          </p>
        </div>
        <button type="button" className="app-button" onClick={() => void loadExports()}>
          <RefreshCcw className="h-4 w-4" aria-hidden="true" />
          Refresh
        </button>
      </div>

      {groupedExports.length === 0 ? (
        <EmptyState
          title="No rendered clips yet"
          action={<Link to={`/projects/${project.id}/editor`} className="app-button app-button-primary">Back to Editor</Link>}
        >
          Accept and render a clip in the editor, then its export will appear here.
        </EmptyState>
      ) : (
        <section className="grid min-w-0 gap-4 xl:grid-cols-2">
          {groupedExports.map((group) => (
            <ExportPanel key={group.key} group={group} />
          ))}
        </section>
      )}
    </div>
  )
}

function ExportPanel({ group }: { group: ExportGroup }) {
  const latestAttempt = group.attempts[0]
  const defaultArtifact = preferredArtifact(latestAttempt.items)
  const [selectedArtifactType, setSelectedArtifactType] = useState(defaultArtifact.artifact_type)

  useEffect(() => {
    setSelectedArtifactType(preferredArtifact(latestAttempt.items).artifact_type)
  }, [latestAttempt.key, latestAttempt.items])

  const selectedArtifact = latestAttempt.items.find((item) => item.artifact_type === selectedArtifactType)
    ?? defaultArtifact
  const previousAttempts = group.attempts.slice(1)

  return (
    <article className="app-panel min-w-0 p-4">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="truncate text-xl font-semibold text-app-text">{group.title}</h2>
          <p className="mt-1 text-sm text-app-muted">Latest render {formatDate(group.latestCreatedAt)}</p>
        </div>
        <span className="inline-flex items-center gap-2 rounded-md border border-app-accent/50 bg-app-accent/15 px-2 py-1 text-xs font-medium text-green-100">
          <FileVideo2 className="h-3.5 w-3.5" aria-hidden="true" />
          Ready
        </span>
      </div>

      <div className="mb-4 flex flex-wrap gap-2" aria-label={`Choose ${group.title} export variant`}>
        {latestAttempt.items
          .slice()
          .sort((a, b) => artifactSortOrder(a.artifact_type) - artifactSortOrder(b.artifact_type))
          .map((item) => (
            <button
              key={item.id}
              type="button"
              className={`inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm transition ${selectedArtifact.id === item.id ? 'border-app-accent bg-app-accent/15 text-app-text' : 'border-app-border bg-app-panelAlt text-app-muted hover:text-app-text'}`}
              aria-label={item.artifact_type === 'subtitled_clip' ? 'With subtitles, recommended' : artifactLabel(item.artifact_type)}
              aria-pressed={selectedArtifact.id === item.id}
              onClick={() => setSelectedArtifactType(item.artifact_type)}
            >
              <span>{artifactLabel(item.artifact_type)}</span>
              {item.artifact_type === 'subtitled_clip' ? (
                <span aria-hidden="true" className="shrink-0 rounded-full border border-app-accent/40 bg-app-accent/10 px-2 py-0.5 text-[11px] font-semibold text-app-accent">
                  Recommended
                </span>
              ) : null}
            </button>
          ))}
      </div>

      <div className="grid min-w-0 gap-4 sm:grid-cols-[minmax(150px,210px)_minmax(0,1fr)]">
        <video
          key={selectedArtifact.id}
          className="aspect-[9/16] max-h-[28rem] w-full rounded-md border border-app-border bg-black object-contain"
          src={apiUrl(selectedArtifact.preview_url)}
          aria-label={`${group.title}, ${artifactLabel(selectedArtifact.artifact_type)} preview`}
          controls
          preload="metadata"
        />
        <div className="grid min-w-0 content-between gap-4">
          <div>
            <p className="app-label">Selected version</p>
            <h3 className="mt-1 text-lg font-semibold text-app-text">{artifactLabel(selectedArtifact.artifact_type)} video</h3>
            {selectedArtifact.artifact_type === 'subtitled_clip' ? (
              <p className="mt-2 text-sm leading-6 text-app-muted">Recommended for publishing with readable, burned-in captions.</p>
            ) : (
              <p className="mt-2 text-sm leading-6 text-app-muted">Clean video without burned-in captions.</p>
            )}
          </div>
          <dl className="grid gap-2 text-sm sm:grid-cols-2">
            <div>
              <dt className="app-label">Duration</dt>
              <dd className="mt-1 text-app-text">{formatSeconds(selectedArtifact.duration)}</dd>
            </div>
            <div>
              <dt className="app-label">File size</dt>
              <dd className="mt-1 text-app-text">{formatFileSize(selectedArtifact.file_size)}</dd>
            </div>
          </dl>
          <a className="app-button app-button-primary w-full" href={apiUrl(selectedArtifact.download_url)}>
            <Download className="h-4 w-4" aria-hidden="true" />
            Download {artifactLabel(selectedArtifact.artifact_type)}
          </a>
          <details className="text-xs text-app-muted">
            <summary className="cursor-pointer">File details</summary>
            <p className="mt-2 break-all">{selectedArtifact.filename}</p>
          </details>
        </div>
      </div>

      {previousAttempts.length > 0 ? (
        <details className="mt-5 border-t border-app-border pt-4">
          <summary className="cursor-pointer text-sm font-semibold text-app-text">
            Previous renders ({previousAttempts.length})
          </summary>
          <div className="mt-3 grid gap-2">
            {previousAttempts.map((attempt) => (
              <div key={attempt.key} className="rounded-md border border-app-border bg-app-panelAlt p-3">
                <p className="text-sm font-medium text-app-text">{formatDate(attempt.createdAt)}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {attempt.items.map((item) => (
                    <a key={item.id} className="app-button" href={apiUrl(item.download_url)}>
                      <Download className="h-4 w-4" aria-hidden="true" />
                      {artifactLabel(item.artifact_type)}
                    </a>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </details>
      ) : null}
    </article>
  )
}

function groupExports(items: ExportItem[]): ExportGroup[] {
  const groups = new Map<string, { key: string; title: string; items: ExportItem[]; latestCreatedAt: string | null }>()
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
      key: group.key,
      title: group.title,
      latestCreatedAt: group.latestCreatedAt,
      attempts: buildRenderAttempts(group.items),
    }))
    .sort((a, b) => new Date(b.latestCreatedAt ?? 0).getTime() - new Date(a.latestCreatedAt ?? 0).getTime())
}

function buildRenderAttempts(items: ExportItem[]): ExportAttempt[] {
  const sortedItems = items.slice().sort((a, b) => {
    const dateDifference = new Date(b.created_at ?? 0).getTime() - new Date(a.created_at ?? 0).getTime()
    return dateDifference || b.id - a.id
  })
  const attempts: ExportAttempt[] = []

  for (const item of sortedItems) {
    const current = attempts.at(-1)
    const currentItem = current?.items[0]
    const pairedArtifactTypes = new Set([currentItem?.artifact_type, item.artifact_type])
    const currentTime = new Date(currentItem?.created_at ?? 0).getTime()
    const itemTime = new Date(item.created_at ?? 0).getTime()
    const timestampsAreClose = (currentItem?.created_at === null && item.created_at === null)
      || Math.abs(currentTime - itemTime) <= 60_000
    const canPairWithCurrent = Boolean(
      current
        && current.items.length === 1
        && currentItem
        && timestampsAreClose
        && pairedArtifactTypes.has('raw_clip')
        && pairedArtifactTypes.has('subtitled_clip'),
    )

    if (current && canPairWithCurrent) {
      current.items.push(item)
      current.items.sort((a, b) => artifactSortOrder(a.artifact_type) - artifactSortOrder(b.artifact_type))
      continue
    }

    attempts.push({
      key: String(item.id),
      items: [item],
      createdAt: item.created_at,
    })
  }

  return attempts
}

function preferredArtifact(items: ExportItem[]): ExportItem {
  return items.find((item) => item.artifact_type === 'subtitled_clip')
    ?? items.find((item) => item.artifact_type === 'raw_clip')
    ?? items[0]
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
