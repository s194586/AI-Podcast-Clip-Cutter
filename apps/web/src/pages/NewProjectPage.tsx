import { ArrowLeft, Loader2, Play, Plus } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { createProject } from '../api/projects'
import { getErrorMessage } from '../api/errors'

function validateYoutubeUrl(value: string): string | null {
  const trimmed = value.trim()
  if (!trimmed) {
    return 'Enter a YouTube URL.'
  }
  try {
    const url = new URL(trimmed)
    const host = url.hostname.replace(/^www\./, '')
    if (url.protocol !== 'http:' && url.protocol !== 'https:') {
      return 'Use an http or https URL.'
    }
    if (!['youtube.com', 'youtu.be', 'm.youtube.com'].includes(host)) {
      return 'Use a YouTube URL.'
    }
    return null
  } catch {
    return 'Enter a valid URL.'
  }
}

export function NewProjectPage() {
  const navigate = useNavigate()
  const [sourceUrl, setSourceUrl] = useState('')
  const [title, setTitle] = useState('')
  const [autoReview, setAutoReview] = useState(true)
  const [submitting, setSubmitting] = useState<'create' | 'start' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const validationError = useMemo(() => validateYoutubeUrl(sourceUrl), [sourceUrl])

  async function submit(autoStart: boolean) {
    const nextError = validateYoutubeUrl(sourceUrl)
    if (nextError) {
      setError(nextError)
      return
    }
    setSubmitting(autoStart ? 'start' : 'create')
    setError(null)
    const controller = new AbortController()
    try {
      const response = await createProject(
        {
          source_url: sourceUrl.trim(),
          title: title.trim() || null,
          auto_review: autoReview,
          auto_start: autoStart,
        },
        controller.signal,
      )
      localStorage.setItem('lastProjectId', String(response.project.id))
      navigate(`/projects/${response.project.id}`)
    } catch (submitError) {
      setError(getErrorMessage(submitError, 'Could not create project.'))
    } finally {
      setSubmitting(null)
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void submit(false)
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <Link to="/" className="inline-flex items-center gap-2 text-sm text-app-muted hover:text-app-text">
        <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        Back to projects
      </Link>

      <section>
        <h1 className="text-3xl font-semibold text-app-text">New project</h1>
        <p className="mt-2 text-sm leading-6 text-app-muted">
          Create an isolated workspace from a YouTube source, then start deterministic processing when you are ready.
        </p>
      </section>

      <form className="app-panel space-y-5 p-5" onSubmit={onSubmit}>
        <div className="space-y-2">
          <label htmlFor="source-url" className="app-label">YouTube URL</label>
          <input
            id="source-url"
            className="app-input"
            value={sourceUrl}
            onChange={(event) => setSourceUrl(event.target.value)}
            placeholder="https://www.youtube.com/watch?v=..."
            aria-invalid={Boolean(error && validationError)}
          />
        </div>
        <div className="space-y-2">
          <label htmlFor="project-title" className="app-label">Project title</label>
          <input
            id="project-title"
            className="app-input"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Optional"
          />
        </div>
        <label className="flex items-start gap-3 rounded-panel border border-app-border bg-app-panelAlt p-4">
          <input
            type="checkbox"
            className="mt-1 h-4 w-4 shrink-0 accent-green-500"
            checked={autoReview}
            onChange={(event) => setAutoReview(event.target.checked)}
          />
          <span>
            <span className="block text-sm font-medium text-app-text">Automatic Gemini review</span>
            <span className="mt-1 block text-sm leading-6 text-app-muted">
              Gemini will review candidate clips and improve their start and end boundaries.
            </span>
          </span>
        </label>

        {error ? <p className="rounded-md border border-app-danger/50 bg-app-danger/10 p-3 text-sm text-red-100">{error}</p> : null}

        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Link to="/" className="app-button">Cancel</Link>
          <button type="submit" className="app-button" disabled={Boolean(submitting)}>
            {submitting === 'create' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Plus className="h-4 w-4" aria-hidden="true" />}
            Create Project
          </button>
          <button type="button" className="app-button app-button-primary" disabled={Boolean(submitting)} onClick={() => void submit(true)}>
            {submitting === 'start' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Play className="h-4 w-4" aria-hidden="true" />}
            Create and Start Processing
          </button>
        </div>
      </form>
    </div>
  )
}
