import {
  ArrowLeft,
  Check,
  FileVideo2,
  Loader2,
  Pause,
  Play,
  RefreshCcw,
  RotateCcw,
  Save,
  Scissors,
  SkipBack,
  Sparkles,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent, ReactNode } from 'react'
import { Link, useOutletContext, useParams } from 'react-router-dom'
import { apiUrl } from '../api/client'
import { acceptProjectClip, listProjectClips, rejectProjectClip, updateProjectClipBounds } from '../api/clips'
import { getErrorMessage } from '../api/errors'
import { getProject } from '../api/projects'
import { renderProjectClip } from '../api/render'
import { reviewProjectClip, reviewProjectClips } from '../api/review'
import type { Clip, Project } from '../api/types'
import type { AppShellContext } from '../components/AppShell'
import { ErrorState, EmptyState, LoadingSkeleton } from '../components/StateBlocks'
import { StatusBadge } from '../components/StatusBadge'
import {
  clipTitle,
  formatSeconds,
  projectTitle,
  reviewerLabel,
  sourceDomain,
  statusLabel,
} from '../utils/format'

type ClipFilter = 'all' | 'adjusted' | 'ready' | 'manual_review' | 'rejected' | 'accepted' | 'rendered'

const CLIP_FILTERS: { id: ClipFilter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'adjusted', label: 'Adjusted' },
  { id: 'ready', label: 'Ready' },
  { id: 'manual_review', label: 'Manual review' },
  { id: 'rejected', label: 'Rejected' },
  { id: 'accepted', label: 'Accepted' },
  { id: 'rendered', label: 'Rendered' },
]

function useNumericProjectId(): number | null {
  const params = useParams()
  const projectId = Number(params.projectId)
  return Number.isInteger(projectId) && projectId > 0 ? projectId : null
}

function clipMatchesFilter(clip: Clip, filter: ClipFilter): boolean {
  if (filter === 'all') {
    return true
  }
  if (filter === 'adjusted') {
    return clip.boundary_source === 'user' || clip.boundary_source === 'ai_review' || Boolean(clip.latest_review_changed_boundaries)
  }
  if (filter === 'ready') {
    return clip.status !== 'rejected' && (clip.latest_review_decision === 'render_ready' || clip.latest_review_decision === 'adjust_boundaries')
  }
  if (filter === 'manual_review') {
    return clip.latest_review_decision === 'manual_review'
  }
  if (filter === 'rendered') {
    return clip.render_status === 'completed' || clip.render_status === 'completed_with_warnings'
  }
  return clip.status === filter
}

function transcriptExcerpt(clip: Clip): string {
  const text = clip.summary.trim() || clip.text.trim()
  if (text.length <= 150) {
    return text
  }
  return `${text.slice(0, 147)}...`
}

function replaceClip(clips: Clip[], nextClip: Clip): Clip[] {
  return clips.map((clip) => (clip.id === nextClip.id ? nextClip : clip))
}

function finiteNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') {
    return null
  }
  const numberValue = Number(value)
  return Number.isFinite(numberValue) ? numberValue : null
}

function preferredSeekStart(clip: Clip): number {
  return finiteNumber(clip.edited_start) ?? finiteNumber(clip.reviewed_start) ?? finiteNumber(clip.ai_start) ?? 0
}

function preferredEditEnd(clip: Clip): number {
  return finiteNumber(clip.edited_end) ?? finiteNumber(clip.reviewed_end) ?? finiteNumber(clip.ai_end) ?? preferredSeekStart(clip)
}

function clampSeekTarget(target: number, video: HTMLVideoElement | null): number {
  const lowerBounded = Math.max(0, target)
  if (!video || !Number.isFinite(video.duration) || video.duration <= 0) {
    return lowerBounded
  }
  return Math.min(lowerBounded, video.duration)
}

const BOUNDARY_VALIDATION_FAILURE_MESSAGE = 'Gemini returned boundaries outside the permitted clip range. This clip requires manual review.'

export function EditorPage() {
  const projectId = useNumericProjectId()
  const { health, healthError } = useOutletContext<AppShellContext>()
  const [project, setProject] = useState<Project | null>(null)
  const [clips, setClips] = useState<Clip[]>([])
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null)
  const [filter, setFilter] = useState<ClipFilter>('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editStart, setEditStart] = useState(0)
  const [editEnd, setEditEnd] = useState(0)
  const [action, setAction] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [currentTime, setCurrentTime] = useState(0)
  const [loopClip, setLoopClip] = useState(false)
  const [videoMissing, setVideoMissing] = useState(false)
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const hydratedClipIdRef = useRef<string | null>(null)
  const pendingSeekTargetRef = useRef<number | null>(null)

  const loadEditor = useCallback(
    async (signal?: AbortSignal) => {
      if (projectId === null) {
        setError('Missing project id.')
        setLoading(false)
        return
      }
      setLoading(true)
      setClips([])
      setSelectedClipId(null)
      try {
        const [projectResponse, clipsResponse] = await Promise.all([
          getProject(projectId, signal),
          listProjectClips(projectId, signal),
        ])
        setProject(projectResponse.project)
        setClips(clipsResponse.clips)
        setSelectedClipId(clipsResponse.clips[0]?.id ?? null)
        setError(null)
        localStorage.setItem('lastProjectId', String(projectId))
      } catch (loadError) {
        if (loadError instanceof DOMException && loadError.name === 'AbortError') {
          return
        }
        setError(getErrorMessage(loadError, 'Could not load project clips.'))
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
    void loadEditor(controller.signal)
    return () => controller.abort()
  }, [loadEditor])

  useEffect(() => {
    pendingSeekTargetRef.current = null
    hydratedClipIdRef.current = null
    setCurrentTime(0)
    setVideoMissing(false)
  }, [projectId])

  const filteredClips = useMemo(() => clips.filter((clip) => clipMatchesFilter(clip, filter)), [clips, filter])
  const selectedClip = clips.find((clip) => clip.id === selectedClipId) ?? filteredClips[0] ?? clips[0] ?? null

  useEffect(() => {
    if (!selectedClip) {
      hydratedClipIdRef.current = null
      return
    }
    if (hydratedClipIdRef.current === selectedClip.id) {
      return
    }
    hydratedClipIdRef.current = selectedClip.id
    const nextStart = preferredSeekStart(selectedClip)
    setEditStart(nextStart)
    setEditEnd(preferredEditEnd(selectedClip))
    requestSeek(nextStart)
    setNotice(null)
    setActionError(null)
  }, [selectedClip])

  useEffect(() => {
    if (!selectedClipId && filteredClips[0]) {
      setSelectedClipId(filteredClips[0].id)
    }
  }, [filteredClips, selectedClipId])

  function updateSelectedClip(nextClip: Clip) {
    setClips((current) => replaceClip(current, nextClip))
    setSelectedClipId(nextClip.id)
  }

  function onRangeChange(event: ChangeEvent<HTMLInputElement>, target: 'start' | 'end') {
    const value = Number(event.target.value)
    if (target === 'start') {
      setEditStart(value)
      if (value >= editEnd) {
        setEditEnd(Number((value + 0.1).toFixed(1)))
      }
      return
    }
    setEditEnd(value)
  }

  function requestSeek(target: number) {
    const video = videoRef.current
    const clampedTarget = clampSeekTarget(target, video)
    setCurrentTime(clampedTarget)
    if (!video || video.readyState < 1) {
      pendingSeekTargetRef.current = target
      return
    }
    video.currentTime = clampedTarget
    pendingSeekTargetRef.current = null
  }

  function applyPendingSeek() {
    const target = pendingSeekTargetRef.current
    if (target === null) {
      return
    }
    requestSeek(target)
  }

  async function saveBoundaries() {
    if (!selectedClip || projectId === null) {
      return
    }
    if (editStart >= editEnd) {
      setActionError('Start time must be before end time.')
      return
    }
    setAction('save')
    setActionError(null)
    setNotice(null)
    try {
      const response = await updateProjectClipBounds(projectId, selectedClip.id, editStart, editEnd)
      updateSelectedClip(response.clip)
      setNotice('Edited boundaries saved.')
    } catch (saveError) {
      setActionError(getErrorMessage(saveError, 'Could not save boundaries.'))
    } finally {
      setAction(null)
    }
  }

  async function setClipStatus(nextStatus: 'accepted' | 'rejected') {
    if (!selectedClip || projectId === null) {
      return
    }
    setAction(nextStatus)
    setActionError(null)
    setNotice(null)
    try {
      const response = nextStatus === 'accepted'
        ? await acceptProjectClip(projectId, selectedClip.id)
        : await rejectProjectClip(projectId, selectedClip.id)
      updateSelectedClip(response.clip)
      setNotice(nextStatus === 'accepted' ? 'Clip accepted.' : 'Clip rejected.')
    } catch (statusError) {
      setActionError(getErrorMessage(statusError, 'Could not update clip status.'))
    } finally {
      setAction(null)
    }
  }

  async function reviewSelectedClip() {
    if (!selectedClip || projectId === null) {
      return
    }
    setAction('review-selected')
    setActionError(null)
    setNotice(null)
    try {
      await reviewProjectClip(projectId, selectedClip.id)
      const response = await listProjectClips(projectId)
      setClips(response.clips)
      setSelectedClipId(selectedClip.id)
      setNotice('Review saved for selected clip.')
    } catch (reviewError) {
      setActionError(getErrorMessage(reviewError, 'Could not review selected clip.'))
    } finally {
      setAction(null)
    }
  }

  async function reviewAllClips() {
    if (projectId === null) {
      return
    }
    setAction('review-all')
    setActionError(null)
    setNotice(null)
    try {
      const result = await reviewProjectClips(projectId)
      const response = await listProjectClips(projectId)
      setClips(response.clips)
      setNotice(`Review completed with ${result.provider ? statusLabel(result.provider) : 'configured reviewer'}.`)
    } catch (reviewError) {
      setActionError(getErrorMessage(reviewError, 'Could not review project clips.'))
    } finally {
      setAction(null)
    }
  }

  async function renderClip() {
    if (!selectedClip || projectId === null) {
      return
    }
    setAction('render')
    setActionError(null)
    setNotice(null)
    try {
      const result = await renderProjectClip(projectId, selectedClip.id, editStart, editEnd)
      updateSelectedClip(result.clip)
      setNotice(result.status === 'completed_with_warnings' ? `Rendered with warnings: ${result.warnings.join(' ')}` : 'Render completed.')
    } catch (renderError) {
      setActionError(getErrorMessage(renderError, 'Render failed.'))
    } finally {
      setAction(null)
    }
  }

  function jumpToStart() {
    requestSeek(editStart)
  }

  async function togglePlayback() {
    const video = videoRef.current
    if (!video) {
      return
    }
    if (video.paused) {
      video.currentTime = video.currentTime < editStart || video.currentTime > editEnd ? editStart : video.currentTime
      await video.play()
    } else {
      video.pause()
    }
  }

  function onVideoTimeUpdate() {
    const video = videoRef.current
    if (!video) {
      return
    }
    setCurrentTime(video.currentTime)
    if (loopClip && selectedClip && video.currentTime >= editEnd) {
      video.currentTime = editStart
      void video.play()
    }
  }

  const renderDisabled = selectedClip?.status === 'rejected' || action === 'render'

  if (loading) {
    return <LoadingSkeleton rows={5} />
  }

  if (error) {
    return <ErrorState title="Editor unavailable" message={error} onRetry={() => void loadEditor()} />
  }

  if (!project) {
    return <ErrorState title="Editor unavailable" message="Project metadata could not be loaded." />
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <Link to={`/projects/${project.id}`} className="inline-flex items-center gap-2 text-sm text-app-muted hover:text-app-text">
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
            Back to processing
          </Link>
          <h1 className="mt-3 text-3xl font-semibold text-app-text">{projectTitle(project)}</h1>
          <p className="mt-2 text-sm text-app-muted">{sourceDomain(project.source_url)}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="app-button" onClick={() => void loadEditor()}>
            <RefreshCcw className="h-4 w-4" aria-hidden="true" />
            Refresh
          </button>
          <Link to={`/projects/${project.id}/exports`} className="app-button">
            <FileVideo2 className="h-4 w-4" aria-hidden="true" />
            Exports
          </Link>
        </div>
      </div>

      {clips.length === 0 ? (
        <EmptyState title="No clips in this project">When processing finishes, candidate clips imported for this project will appear here.</EmptyState>
      ) : null}

      {clips.length > 0 && selectedClip ? (
        <section className="grid min-w-0 gap-4 xl:grid-cols-[minmax(260px,330px)_minmax(0,1fr)_minmax(280px,360px)]">
          <aside className="app-panel min-w-0 overflow-hidden">
            <div className="border-b border-app-border p-4">
              <h2 className="app-section-title">Clip list</h2>
              <div className="mt-3 flex flex-wrap gap-2">
                {CLIP_FILTERS.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`rounded-md border px-2.5 py-1.5 text-xs transition ${filter === item.id ? 'border-app-accent bg-app-accent/15 text-app-text' : 'border-app-border bg-app-panelAlt text-app-muted hover:text-app-text'}`}
                    onClick={() => setFilter(item.id)}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="max-h-[720px] overflow-auto p-2">
              {filteredClips.length === 0 ? (
                <p className="p-4 text-sm text-app-muted">No clips match this filter.</p>
              ) : (
                filteredClips.map((clip) => (
                  <button
                    key={clip.id}
                    type="button"
                    className={`mb-2 w-full rounded-panel border p-3 text-left transition ${clip.id === selectedClip.id ? 'border-app-accent bg-app-accent/10' : 'border-app-border bg-app-panelAlt hover:border-app-muted'}`}
                    onClick={() => setSelectedClipId(clip.id)}
                  >
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <span className="font-semibold text-app-text">{clipTitle(clip)}</span>
                      <span className="text-xs text-app-muted">{formatSeconds(clip.duration)}</span>
                    </div>
                    <p className="line-clamp-3 text-sm leading-5 text-app-muted">{transcriptExcerpt(clip) || 'No transcript excerpt.'}</p>
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      <StatusBadge value={clip.status} />
                      <StatusBadge value={clip.latest_review_decision ?? 'draft'} />
                      <StatusBadge value={clip.render_status} />
                    </div>
                    <dl className="mt-3 grid grid-cols-2 gap-2 text-xs text-app-faint">
                      <div>
                        <dt>Provider</dt>
                        <dd className="text-app-muted">{clip.latest_review_provider ? statusLabel(clip.latest_review_provider) : 'None'}</dd>
                      </div>
                      <div>
                        <dt>Boundary</dt>
                        <dd className="text-app-muted">{statusLabel(clip.boundary_source)}</dd>
                      </div>
                    </dl>
                  </button>
                ))
              )}
            </div>
          </aside>

          <main className="min-w-0 space-y-4">
            <section className="app-panel p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="app-section-title">Video workspace</h2>
                  <p className="mt-1 text-sm text-app-muted">Clip interval {formatSeconds(editStart)} to {formatSeconds(editEnd)}</p>
                </div>
                <StatusBadge value={selectedClip.status} />
              </div>
              {videoMissing ? (
                <div className="flex aspect-video items-center justify-center rounded-panel border border-app-border bg-black/40 text-sm text-app-muted">
                  Missing source video for this project.
                </div>
              ) : (
                <video
                  ref={videoRef}
                  className="aspect-video w-full rounded-panel border border-app-border bg-black"
                  src={apiUrl(`/projects/${project.id}/source-video`)}
                  controls
                  preload="metadata"
                  onError={() => setVideoMissing(true)}
                  onLoadedMetadata={applyPendingSeek}
                  onCanPlay={applyPendingSeek}
                  onTimeUpdate={onVideoTimeUpdate}
                />
              )}
              <div className="mt-4 grid gap-3 md:grid-cols-[1fr_auto] md:items-center">
                <div className="grid gap-2 sm:grid-cols-4">
                  <div className="app-panel-muted p-3">
                    <p className="app-label">Current time</p>
                    <p className="mt-1 font-semibold">{formatSeconds(currentTime)}</p>
                  </div>
                  <div className="app-panel-muted p-3">
                    <p className="app-label">Start time</p>
                    <p className="mt-1 font-semibold">{formatSeconds(editStart)}</p>
                  </div>
                  <div className="app-panel-muted p-3">
                    <p className="app-label">End time</p>
                    <p className="mt-1 font-semibold">{formatSeconds(editEnd)}</p>
                  </div>
                  <div className="app-panel-muted p-3">
                    <p className="app-label">Duration</p>
                    <p className="mt-1 font-semibold">{formatSeconds(editEnd - editStart)}</p>
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button type="button" className="app-button" onClick={() => void togglePlayback()}>
                    <Play className="h-4 w-4" aria-hidden="true" />
                    <Pause className="h-4 w-4" aria-hidden="true" />
                    Play/Pause
                  </button>
                  <button type="button" className="app-button" onClick={jumpToStart}>
                    <SkipBack className="h-4 w-4" aria-hidden="true" />
                    Jump to Start
                  </button>
                  <button type="button" className={`app-button ${loopClip ? 'border-app-accent text-app-text' : ''}`} onClick={() => setLoopClip((value) => !value)}>
                    <RotateCcw className="h-4 w-4" aria-hidden="true" />
                    Loop Clip Preview
                  </button>
                </div>
              </div>
            </section>

            <section className="app-panel p-4">
              <h2 className="app-section-title">Boundary editor</h2>
              <div className="mt-4 grid gap-3 md:grid-cols-3">
                <BoundaryGroup title="Original selection" start={selectedClip.ai_start} end={selectedClip.ai_end} />
                <BoundaryGroup title="Gemini suggestion" start={selectedClip.reviewed_start} end={selectedClip.reviewed_end} emptyLabel="Not reviewed yet" />
                <BoundaryGroup title="Current edit" start={editStart} end={editEnd} />
              </div>
              <div className="mt-5 grid gap-4">
                <label className="space-y-2">
                  <span className="app-label">Start range control</span>
                  <input
                    type="range"
                    min={selectedClip.min_start}
                    max={Math.min(selectedClip.max_start, editEnd - 0.1)}
                    step="0.1"
                    value={editStart}
                    onChange={(event) => onRangeChange(event, 'start')}
                    className="w-full accent-green-500"
                  />
                </label>
                <label className="space-y-2">
                  <span className="app-label">End range control</span>
                  <input
                    type="range"
                    min={Math.max(selectedClip.min_end, editStart + 0.1)}
                    max={selectedClip.max_end}
                    step="0.1"
                    value={editEnd}
                    onChange={(event) => onRangeChange(event, 'end')}
                    className="w-full accent-green-500"
                  />
                </label>
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="space-y-2">
                    <span className="app-label">Numeric start input</span>
                    <input type="number" className="app-input" step="0.1" value={editStart} onChange={(event) => setEditStart(Number(event.target.value))} />
                  </label>
                  <label className="space-y-2">
                    <span className="app-label">Numeric end input</span>
                    <input type="number" className="app-input" step="0.1" value={editEnd} onChange={(event) => setEditEnd(Number(event.target.value))} />
                  </label>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button type="button" className="app-button" disabled={selectedClip.reviewed_start === null || selectedClip.reviewed_end === null} onClick={() => {
                    const nextStart = selectedClip.reviewed_start ?? selectedClip.ai_start
                    const nextEnd = selectedClip.reviewed_end ?? selectedClip.ai_end
                    setEditStart(nextStart)
                    setEditEnd(nextEnd)
                    requestSeek(nextStart)
                  }}>
                    <Sparkles className="h-4 w-4" aria-hidden="true" />
                    Reset to Gemini suggestion
                  </button>
                  <button type="button" className="app-button" onClick={() => {
                    setEditStart(selectedClip.ai_start)
                    setEditEnd(selectedClip.ai_end)
                    requestSeek(selectedClip.ai_start)
                  }}>
                    <Scissors className="h-4 w-4" aria-hidden="true" />
                    Reset to original selection
                  </button>
                  <button type="button" className="app-button app-button-primary" disabled={action === 'save'} onClick={() => void saveBoundaries()}>
                    {action === 'save' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Save className="h-4 w-4" aria-hidden="true" />}
                    Save Boundaries
                  </button>
                </div>
              </div>
            </section>
          </main>

          <aside className="min-w-0 space-y-4">
            <section className="app-panel p-4">
              <h2 className="app-section-title">Actions</h2>
              <div className="mt-4 grid gap-2">
                <button type="button" className="app-button app-button-primary" disabled={action === 'accepted'} onClick={() => void setClipStatus('accepted')}>
                  {action === 'accepted' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Check className="h-4 w-4" aria-hidden="true" />}
                  Accept
                </button>
                <button type="button" className="app-button app-button-danger" disabled={action === 'rejected'} onClick={() => void setClipStatus('rejected')}>
                  {action === 'rejected' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <X className="h-4 w-4" aria-hidden="true" />}
                  Reject
                </button>
                <button type="button" className="app-button" disabled={renderDisabled} onClick={() => void renderClip()}>
                  {action === 'render' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <FileVideo2 className="h-4 w-4" aria-hidden="true" />}
                  Render Short
                </button>
                <button type="button" className="app-button" disabled={action === 'review-selected'} onClick={() => void reviewSelectedClip()}>
                  {action === 'review-selected' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Sparkles className="h-4 w-4" aria-hidden="true" />}
                  Review selected clip
                </button>
                <button type="button" className="app-button" disabled={action === 'review-all'} onClick={() => void reviewAllClips()}>
                  {action === 'review-all' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Sparkles className="h-4 w-4" aria-hidden="true" />}
                  {health?.clip_review_provider === 'gemini' ? 'Review all clips with Gemini' : 'Review all clips with configured reviewer'}
                </button>
              </div>
              {selectedClip.status === 'rejected' ? <p className="mt-3 text-sm leading-6 text-app-muted">Change the clip decision before rendering this rejected clip.</p> : null}
              {notice ? <p className="mt-3 rounded-md border border-app-accent/50 bg-app-accent/10 p-3 text-sm text-green-100">{notice}</p> : null}
              {actionError ? <p className="mt-3 rounded-md border border-app-danger/50 bg-app-danger/10 p-3 text-sm text-red-100">{actionError}</p> : null}
            </section>

            <section className="app-panel p-4">
              <h2 className="app-section-title">AI review panel</h2>
              <dl className="mt-4 space-y-3 text-sm">
                <InfoRow label="Configured reviewer" value={healthError ? 'Backend unavailable' : reviewerLabel(health)} />
                <InfoRow label="Latest saved provider" value={selectedClip.latest_review_provider ? statusLabel(selectedClip.latest_review_provider) : 'No saved review'} />
                <InfoRow label="Review decision" value={<StatusBadge value={selectedClip.latest_review_decision ?? 'draft'} />} />
                <InfoRow label="Boundaries changed" value={selectedClip.latest_review_changed_boundaries ? 'Yes' : 'No'} />
                <InfoRow label="Manual-review state" value={selectedClip.latest_review_decision === 'manual_review' ? 'Needs manual review' : 'Not flagged'} />
                <InfoRow label="Technical failure state" value={selectedClip.latest_review_failed ? 'Requires attention' : 'None'} />
              </dl>
              {selectedClip.latest_review_failed ? (
                <div className="mt-4 rounded-md border border-app-danger/50 bg-app-danger/10 p-3 text-sm text-red-100">
                  <p>
                    {selectedClip.latest_review_failure_category === 'boundary_validation'
                      ? BOUNDARY_VALIDATION_FAILURE_MESSAGE
                      : selectedClip.latest_review_reasoning_summary || 'The saved review could not be safely applied.'}
                  </p>
                  {(selectedClip.latest_review_warnings?.length ?? 0) > 0 ? (
                    <details className="mt-3 border-t border-app-danger/30 pt-3 text-app-muted">
                      <summary className="cursor-pointer font-medium text-app-text">Technical details</summary>
                      <p className="mt-2 break-words leading-6">{selectedClip.latest_review_warnings?.join(' ')}</p>
                    </details>
                  ) : null}
                </div>
              ) : null}
              <div className="mt-4 rounded-panel border border-app-border bg-app-panelAlt p-3">
                <p className="app-label">Concise rationale</p>
                <p className="mt-2 text-sm leading-6 text-app-muted">{selectedClip.latest_review_reasoning_summary || 'No saved rationale yet.'}</p>
              </div>
            </section>
          </aside>
        </section>
      ) : null}
    </div>
  )
}

interface BoundaryGroupProps {
  title: string
  start: number | null
  end: number | null
  emptyLabel?: string
}

function BoundaryGroup({ title, start, end, emptyLabel }: BoundaryGroupProps) {
  const hasValues = start !== null && end !== null
  return (
    <div className="app-panel-muted p-3">
      <h3 className="text-sm font-semibold text-app-text">{title}</h3>
      {hasValues ? (
        <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
          <div>
            <dt className="app-label">Start</dt>
            <dd className="mt-1 font-semibold">{formatSeconds(start)}</dd>
          </div>
          <div>
            <dt className="app-label">End</dt>
            <dd className="mt-1 font-semibold">{formatSeconds(end)}</dd>
          </div>
        </dl>
      ) : (
        <p className="mt-3 text-sm text-app-muted">{emptyLabel ?? 'No values'}</p>
      )}
    </div>
  )
}

interface InfoRowProps {
  label: string
  value: ReactNode
}

function InfoRow({ label, value }: InfoRowProps) {
  return (
    <div className="grid grid-cols-[140px_1fr] gap-3">
      <dt className="text-app-muted">{label}</dt>
      <dd className="min-w-0 text-app-text">{value}</dd>
    </div>
  )
}
