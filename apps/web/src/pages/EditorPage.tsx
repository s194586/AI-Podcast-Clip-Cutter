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
  statusLabel,
} from '../utils/format'

type ClipFilter = 'all' | 'review' | 'accepted' | 'rendered' | 'rejected'
type ReviewScope = 'selected' | 'all'

const CLIP_FILTERS: { id: ClipFilter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'review', label: 'Needs review' },
  { id: 'accepted', label: 'Accepted' },
  { id: 'rendered', label: 'Rendered' },
  { id: 'rejected', label: 'Rejected' },
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
  if (filter === 'rendered') {
    return clip.render_status === 'completed' || clip.render_status === 'completed_with_warnings'
  }
  if (filter === 'review') {
    return clip.status !== 'accepted' && clip.status !== 'rejected'
  }
  return clip.status === filter
}

function clipWorkflowState(clip: Clip): string {
  if (clip.render_status === 'completed' || clip.render_status === 'completed_with_warnings') {
    return clip.render_status
  }
  if (clip.status === 'accepted' || clip.status === 'rejected') {
    return clip.status
  }
  if (clip.latest_review_decision === 'manual_review') {
    return 'manual_review'
  }
  if (
    clip.boundary_source === 'user'
    || clip.boundary_source === 'ai_review'
    || clip.latest_review_decision === 'adjust_boundaries'
  ) {
    return 'adjust_boundaries'
  }
  return 'draft'
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
  const [pendingReview, setPendingReview] = useState<ReviewScope | null>(null)
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const confirmReviewRef = useRef<HTMLButtonElement | null>(null)
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
  const selectedClip = filteredClips.find((clip) => clip.id === selectedClipId) ?? filteredClips[0] ?? clips[0] ?? null

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
    if (filteredClips[0] && !filteredClips.some((clip) => clip.id === selectedClipId)) {
      setSelectedClipId(filteredClips[0].id)
    }
  }, [filteredClips, selectedClipId])

  useEffect(() => {
    if (!pendingReview) {
      return undefined
    }
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null
    confirmReviewRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setPendingReview(null)
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      previouslyFocused?.focus()
    }
  }, [pendingReview])

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
      setNotice(
        result.summary_message
          || `${result.provider ? statusLabel(result.provider) : 'Configured reviewer'} review finished: `
            + `${result.applied_count ?? 0} applied, `
            + `${result.requires_attention_count ?? result.failed_count ?? 0} require attention.`,
      )
    } catch (reviewError) {
      setActionError(getErrorMessage(reviewError, 'Could not review project clips.'))
    } finally {
      setAction(null)
    }
  }

  async function confirmReview() {
    const scope = pendingReview
    setPendingReview(null)
    if (scope === 'selected') {
      await reviewSelectedClip()
    } else if (scope === 'all') {
      await reviewAllClips()
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

  const isRendered = selectedClip?.render_status === 'completed' || selectedClip?.render_status === 'completed_with_warnings'
  const isAccepted = selectedClip?.status === 'accepted'
  const actionBusy = action !== null
  const hasSavedReview = Boolean(
    selectedClip
      && (
        selectedClip.latest_review_provider
        || (selectedClip.reviewed_start !== null && selectedClip.reviewed_end !== null)
      ),
  )

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
          <p className="mt-2 text-sm text-app-muted">Review clip boundaries, choose a decision, then render the final short.</p>
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

      <nav className="app-panel-muted overflow-x-auto p-3" aria-label="Clip production steps">
        <ol className="flex min-w-max items-center gap-2 text-sm">
          {['1. Preview', '2. Edit boundaries', '3. Accept', '4. Render', '5. Export'].map((step, index) => (
            <li key={step} className={`rounded-md px-3 py-2 ${index === 0 || (index <= 2 && isAccepted) || (index <= 4 && isRendered) ? 'bg-app-accent/10 text-app-text' : 'text-app-muted'}`}>
              {step}
            </li>
          ))}
        </ol>
      </nav>

      {clips.length === 0 ? (
        <EmptyState title="No clips in this project">When processing finishes, candidate clips imported for this project will appear here.</EmptyState>
      ) : null}

      {clips.length > 0 && selectedClip ? (
        <section className="grid min-w-0 gap-4 xl:grid-cols-[minmax(240px,300px)_minmax(420px,1fr)_minmax(260px,320px)]">
          <aside className="app-panel min-w-0 overflow-hidden">
            <div className="border-b border-app-border p-4">
              <h2 className="app-section-title">Clip list</h2>
              <div className="mt-3 flex flex-wrap gap-2">
                {CLIP_FILTERS.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className={`rounded-md border px-2.5 py-1.5 text-xs transition ${filter === item.id ? 'border-app-accent bg-app-accent/15 text-app-text' : 'border-app-border bg-app-panelAlt text-app-muted hover:text-app-text'}`}
                    aria-pressed={filter === item.id}
                    onClick={() => setFilter(item.id)}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="max-h-[420px] overflow-auto p-2 xl:max-h-[calc(100vh-19rem)]">
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
                    <div className="mt-3">
                      <StatusBadge value={clipWorkflowState(clip)} />
                    </div>
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
                <div className="flex aspect-video w-full items-center justify-center rounded-panel border border-app-border bg-black/40 text-sm text-app-muted 2xl:max-h-[46vh]">
                  Missing source video for this project.
                </div>
              ) : (
                <video
                  ref={videoRef}
                  className="aspect-video w-full rounded-panel border border-app-border bg-black object-contain 2xl:max-h-[46vh]"
                  src={apiUrl(`/projects/${project.id}/source-video`)}
                  controls
                  preload="metadata"
                  onError={() => setVideoMissing(true)}
                  onLoadedMetadata={applyPendingSeek}
                  onCanPlay={applyPendingSeek}
                  onTimeUpdate={onVideoTimeUpdate}
                />
              )}
              <div className="mt-3 border-t border-app-border pt-3">
                <div className="flex flex-wrap items-end justify-between gap-3">
                  <div>
                    <h3 className="app-section-title">Selection boundaries</h3>
                    <p className="mt-1 text-sm text-app-muted">Adjust the exact start and end used for preview and render.</p>
                  </div>
                  <p className="text-sm text-app-muted">
                    <span className="app-label mr-2">Current time</span>
                    <span className="font-semibold text-app-text">{formatSeconds(currentTime)}</span>
                  </p>
                </div>

                <div className="mt-3 grid items-end gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
                  <label className="min-w-0 space-y-2">
                    <span className="app-label">Numeric start input</span>
                    <input type="number" className="app-input" step="0.1" value={editStart} onChange={(event) => setEditStart(Number(event.target.value))} />
                  </label>
                  <label className="min-w-0 space-y-2">
                    <span className="app-label">Numeric end input</span>
                    <input type="number" className="app-input" step="0.1" value={editEnd} onChange={(event) => setEditEnd(Number(event.target.value))} />
                  </label>
                  <button type="button" className="app-button app-button-primary" onClick={jumpToStart}>
                    <SkipBack className="h-4 w-4" aria-hidden="true" />
                    Preview selection
                  </button>
                </div>

                <dl className="mt-4 grid grid-cols-3 gap-2">
                  <div className="app-panel-muted p-3">
                    <dt className="app-label">Start time</dt>
                    <dd className="mt-1 font-semibold">{formatSeconds(editStart)}</dd>
                  </div>
                  <div className="app-panel-muted p-3">
                    <dt className="app-label">End time</dt>
                    <dd className="mt-1 font-semibold">{formatSeconds(editEnd)}</dd>
                  </div>
                  <div className="app-panel-muted p-3">
                    <dt className="app-label">Duration</dt>
                    <dd className="mt-1 font-semibold">{formatSeconds(editEnd - editStart)}</dd>
                  </div>
                </dl>

                <div className="mt-4 grid gap-4">
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
                <div className="flex flex-wrap gap-2">
                  <button type="button" className="app-button" onClick={() => void togglePlayback()}>
                    <Play className="h-4 w-4" aria-hidden="true" />
                    <Pause className="h-4 w-4" aria-hidden="true" />
                    Play/Pause
                  </button>
                  <button type="button" className={`app-button ${loopClip ? 'border-app-accent text-app-text' : ''}`} aria-pressed={loopClip} onClick={() => setLoopClip((value) => !value)}>
                    <RotateCcw className="h-4 w-4" aria-hidden="true" />
                    Loop Preview
                  </button>
                  <button type="button" className="app-button" onClick={() => requestSeek(selectedClip.ai_start)}>
                    <Play className="h-4 w-4" aria-hidden="true" />
                    Preview original
                  </button>
                  <button
                    type="button"
                    className="app-button"
                    disabled={selectedClip.reviewed_start === null}
                    onClick={() => requestSeek(selectedClip.reviewed_start ?? selectedClip.ai_start)}
                  >
                    <Sparkles className="h-4 w-4" aria-hidden="true" />
                    Preview Gemini
                  </button>
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
                  <button type="button" className="app-button app-button-primary" disabled={actionBusy || editStart >= editEnd} onClick={() => void saveBoundaries()}>
                    {action === 'save' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Save className="h-4 w-4" aria-hidden="true" />}
                    Save Boundaries
                  </button>
                </div>

                <div className="grid gap-3 border-t border-app-border pt-4 md:grid-cols-3">
                  <BoundaryGroup title="Original selection" start={selectedClip.ai_start} end={selectedClip.ai_end} />
                  <BoundaryGroup title="Gemini suggestion" start={selectedClip.reviewed_start} end={selectedClip.reviewed_end} emptyLabel="Not reviewed yet" />
                  <BoundaryGroup title="Current edit" start={editStart} end={editEnd} />
                </div>
                </div>
              </div>
            </section>
          </main>

          <aside className="min-w-0 space-y-4">
            <section className="app-panel p-4">
              <h2 className="app-section-title">Next action</h2>
              <p className="mt-2 text-sm leading-6 text-app-muted">
                {isRendered
                  ? 'This clip has a completed export. Open it or create a new render attempt.'
                  : isAccepted
                    ? 'This clip is accepted and ready to render.'
                    : selectedClip.status === 'rejected'
                      ? 'This clip is rejected. Accept it again before rendering.'
                      : 'Preview the boundaries, save any edits, then accept or reject the clip.'}
              </p>
              <div className="mt-4 grid gap-2">
                {isRendered ? (
                  <>
                    <Link to={`/projects/${project.id}/exports`} className="app-button app-button-primary">
                      <FileVideo2 className="h-4 w-4" aria-hidden="true" />
                      View Export
                    </Link>
                    <button type="button" className="app-button" disabled={actionBusy} onClick={() => void renderClip()}>
                      {action === 'render' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <RotateCcw className="h-4 w-4" aria-hidden="true" />}
                      {action === 'render' ? 'Rendering...' : 'Re-render Short'}
                    </button>
                  </>
                ) : isAccepted ? (
                  <>
                    <button type="button" className="app-button app-button-primary" disabled={actionBusy} onClick={() => void renderClip()}>
                      {action === 'render' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <FileVideo2 className="h-4 w-4" aria-hidden="true" />}
                      {action === 'render' ? 'Rendering...' : 'Render Short'}
                    </button>
                    <button type="button" className="app-button app-button-danger" disabled={actionBusy} onClick={() => void setClipStatus('rejected')}>
                      {action === 'rejected' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <X className="h-4 w-4" aria-hidden="true" />}
                      Reject Clip
                    </button>
                  </>
                ) : selectedClip.status === 'rejected' ? (
                  <button type="button" className="app-button app-button-primary" disabled={actionBusy} onClick={() => void setClipStatus('accepted')}>
                    {action === 'accepted' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Check className="h-4 w-4" aria-hidden="true" />}
                    Accept Clip
                  </button>
                ) : (
                  <>
                    <button type="button" className="app-button app-button-primary" disabled={actionBusy} onClick={() => void setClipStatus('accepted')}>
                      {action === 'accepted' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Check className="h-4 w-4" aria-hidden="true" />}
                      Accept Clip
                    </button>
                    <button type="button" className="app-button app-button-danger" disabled={actionBusy} onClick={() => void setClipStatus('rejected')}>
                      {action === 'rejected' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <X className="h-4 w-4" aria-hidden="true" />}
                      Reject Clip
                    </button>
                  </>
                )}
              </div>
              {action === 'render' ? (
                <p className="mt-3 rounded-md border border-app-accent/50 bg-app-accent/10 p-3 text-sm text-green-100" role="status">
                  Rendering your short. This may take a moment.
                </p>
              ) : null}
              {notice ? <p className="mt-3 rounded-md border border-app-accent/50 bg-app-accent/10 p-3 text-sm text-green-100" role="status">{notice}</p> : null}
              {actionError ? <p className="mt-3 rounded-md border border-app-danger/50 bg-app-danger/10 p-3 text-sm text-red-100" role="alert">{actionError}</p> : null}
            </section>

            <section className="app-panel p-4">
              <h2 className="app-section-title">AI review panel</h2>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <StatusBadge value={selectedClip.latest_review_decision ?? 'draft'} />
                <span className="text-sm text-app-muted">
                  {selectedClip.latest_review_decision === 'manual_review'
                    ? 'Needs manual review'
                    : selectedClip.latest_review_changed_boundaries
                      ? 'Suggested different boundaries'
                      : 'No boundary change suggested'}
                </span>
              </div>
              {selectedClip.latest_review_failed ? (
                <div className="mt-4 rounded-md border border-app-danger/50 bg-app-danger/10 p-3 text-sm text-red-100" role="alert">
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
              <div className="mt-4 grid gap-2 border-t border-app-border pt-4">
                <button type="button" className="app-button" disabled={actionBusy} onClick={() => setPendingReview('selected')}>
                  {action === 'review-selected' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Sparkles className="h-4 w-4" aria-hidden="true" />}
                  {hasSavedReview ? 'Re-run AI Review for This Clip' : 'Run AI Review for This Clip'}
                </button>
                <button type="button" className="app-button" disabled={actionBusy} onClick={() => setPendingReview('all')}>
                  {action === 'review-all' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Sparkles className="h-4 w-4" aria-hidden="true" />}
                  {health?.clip_review_provider === 'gemini' ? 'Re-review All Clips with Gemini' : 'Re-review All Clips'}
                </button>
              </div>
              <details className="mt-4 border-t border-app-border pt-4">
                <summary className="cursor-pointer text-sm font-medium text-app-text">Review rationale and details</summary>
                <div className="mt-4 rounded-panel border border-app-border bg-app-panelAlt p-3">
                  <p className="app-label">Review rationale</p>
                  <p className="mt-2 text-sm leading-6 text-app-muted">{selectedClip.latest_review_reasoning_summary || 'No saved rationale yet.'}</p>
                </div>
                <dl className="mt-4 space-y-3 text-sm">
                  <InfoRow label="Configured reviewer" value={healthError ? 'Backend unavailable' : reviewerLabel(health)} />
                  <InfoRow label="Latest provider" value={selectedClip.latest_review_provider ? statusLabel(selectedClip.latest_review_provider) : 'No saved review'} />
                  <InfoRow label="Boundary source" value={statusLabel(selectedClip.boundary_source)} />
                  <InfoRow label="Manual review" value={selectedClip.latest_review_decision === 'manual_review' ? 'Required' : 'Not flagged'} />
                  <InfoRow label="Failure state" value={selectedClip.latest_review_failed ? 'Requires attention' : 'None'} />
                </dl>
              </details>
            </section>
          </aside>
        </section>
      ) : null}

      {pendingReview ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4" role="presentation">
          <section
            className="app-panel w-full max-w-lg p-5"
            role="dialog"
            aria-modal="true"
            aria-labelledby="review-confirmation-title"
            aria-describedby="review-confirmation-description"
          >
            <h2 id="review-confirmation-title" className="text-xl font-semibold text-app-text">
              {pendingReview === 'all' ? 'Re-review all clips?' : hasSavedReview ? 'Replace the saved AI review?' : 'Run AI boundary review?'}
            </h2>
            <p id="review-confirmation-description" className="mt-3 text-sm leading-6 text-app-muted">
              {pendingReview === 'all'
                ? 'This runs the configured reviewer for every clip and replaces saved suggestions. Gemini usage may incur API cost.'
                : 'This runs the configured reviewer for the selected clip and replaces its saved suggestion. Gemini usage may incur API cost.'}
            </p>
            <div className="mt-5 flex flex-wrap justify-end gap-2">
              <button type="button" className="app-button" onClick={() => setPendingReview(null)}>
                Cancel
              </button>
              <button ref={confirmReviewRef} type="button" className="app-button app-button-primary" onClick={() => void confirmReview()}>
                <Sparkles className="h-4 w-4" aria-hidden="true" />
                Run Review
              </button>
            </div>
          </section>
        </div>
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
