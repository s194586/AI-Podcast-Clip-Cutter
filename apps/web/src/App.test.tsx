import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AppRoutes } from './App'
import type { Clip, ExportItem, HealthStatus, Project, ProjectStatus } from './api/types'
import { mockApi, renderApp } from './test/test-utils'

const healthGemini: HealthStatus = {
  status: 'ok',
  clip_review_provider: 'gemini',
  clip_review_model: 'gemini-3.5-flash',
  clip_review_mode_source: 'env',
  gemini_api_key_configured: true,
}

const healthLocal: HealthStatus = {
  status: 'ok',
  clip_review_provider: 'local_stub',
  clip_review_model: 'local-stub',
  clip_review_mode_source: 'default',
  gemini_api_key_configured: false,
}

function project(overrides: Partial<Project> = {}): Project {
  return {
    id: 3,
    title: 'Deep Podcast',
    source_url: 'https://www.youtube.com/watch?v=project3',
    status: 'ready',
    current_stage: 'ready',
    stage: 'ready',
    progress_percent: 100,
    auto_review: true,
    created_at: '2026-07-16T10:00:00Z',
    updated_at: '2026-07-16T12:00:00Z',
    clip_count: 2,
    accepted_clip_count: 1,
    ...overrides,
  }
}

function projectStatus(overrides: Partial<ProjectStatus> = {}): ProjectStatus {
  return {
    project_id: 3,
    status: 'ready',
    stage: 'ready',
    current_stage: 'ready',
    progress_percent: 100,
    message: 'Ready for review',
    error_message: null,
    updated_at: '2026-07-16T12:00:00Z',
    clip_count: 2,
    last_error: null,
    job: null,
    ...overrides,
  }
}

function clip(overrides: Partial<Clip> = {}): Clip {
  return {
    id: 'clip_001',
    database_id: 101,
    project_id: 3,
    index: 1,
    ai_start: 10,
    ai_end: 18,
    reviewed_start: 10.5,
    reviewed_end: 18.5,
    edited_start: 10.5,
    edited_end: 18.5,
    boundary_source: 'ai_review',
    min_start: 8,
    max_start: 17,
    min_end: 11,
    max_end: 24,
    duration: 8,
    summary: 'A concise transcript excerpt about the main argument.',
    text: 'A concise transcript excerpt about the main argument.',
    status: 'draft',
    candidate_id: 'candidate-1',
    selection_source: 'local',
    selection_reasons: ['clear hook'],
    render_status: 'not_rendered',
    latest_review_provider: 'gemini',
    latest_review_model: 'gemini-3.5-flash',
    latest_review_decision: 'render_ready',
    latest_review_recommended_action: 'render_ready',
    latest_review_reasoning_summary: 'The clip stands alone cleanly.',
    latest_review_start_reason: 'Starts at a complete thought.',
    latest_review_end_reason: 'Ends after the payoff.',
    latest_review_warnings: [],
    latest_review_changed_boundaries: true,
    created_at: '2026-07-16T10:30:00Z',
    updated_at: '2026-07-16T11:00:00Z',
    ...overrides,
  }
}

function exportItem(overrides: Partial<ExportItem> = {}): ExportItem {
  return {
    id: 501,
    project_id: 3,
    clip_id: 'clip_001',
    clip_database_id: 101,
    clip_index: 1,
    artifact_type: 'subtitled_clip',
    filename: 'segment_001.mp4',
    media_type: 'video/mp4',
    created_at: '2026-07-16T12:15:00Z',
    duration: 8,
    file_size: 1048576,
    download_url: '/projects/3/exports/501/download',
    preview_url: '/projects/3/exports/501/download',
    ...overrides,
  }
}

function editorRoutes(clips = [clip()], health = healthGemini) {
  return [
    { path: '/health', json: health },
    { path: '/projects/3', json: { project: project() } },
    { path: '/projects/3/clips', json: { clips } },
  ]
}

function getRenderedVideo(container: HTMLElement): HTMLVideoElement {
  const video = container.querySelector('video')
  expect(video).not.toBeNull()
  return video as HTMLVideoElement
}

function markVideoReady(video: HTMLVideoElement, duration = 180) {
  Object.defineProperty(video, 'readyState', { configurable: true, value: 1 })
  Object.defineProperty(video, 'duration', { configurable: true, value: duration })
}

describe('Product UI', () => {
  it('controls the system status popover with ARIA and Escape', async () => {
    const user = userEvent.setup()
    mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects', json: { projects: [project()] } },
    ])

    renderApp('/')

    const trigger = await screen.findByRole('button', { name: 'System ready' })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(trigger).toHaveAttribute('aria-controls', 'system-status-panel')
    expect(screen.queryByRole('region', { name: 'Technical status' })).not.toBeInTheDocument()

    await user.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('region', { name: 'Technical status' })).toBeInTheDocument()

    await user.keyboard('{Escape}')
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('region', { name: 'Technical status' })).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })

  it('closes the system status popover after an outside click', async () => {
    const user = userEvent.setup()
    mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects', json: { projects: [project()] } },
    ])

    renderApp('/')

    const trigger = await screen.findByRole('button', { name: 'System ready' })
    await user.click(trigger)
    await user.click(screen.getByRole('heading', { name: 'Projects' }))

    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('region', { name: 'Technical status' })).not.toBeInTheDocument()
  })

  it('closes the system status popover after a route change', async () => {
    mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects', json: { projects: [project()] } },
    ])

    renderApp('/')

    const trigger = await screen.findByRole('button', { name: 'System ready' })
    fireEvent.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'true')

    fireEvent.click(screen.getAllByRole('link', { name: 'New Project' })[0])
    expect(await screen.findByRole('heading', { name: 'New project' })).toBeInTheDocument()
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('region', { name: 'Technical status' })).not.toBeInTheDocument()
  })

  it('loads projects on the dashboard', async () => {
    mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects', json: { projects: [project()] } },
    ])

    renderApp('/')

    expect(await screen.findByText('Deep Podcast')).toBeInTheDocument()
    expect(screen.getByText('Total projects')).toBeInTheDocument()
    expect(screen.getByText(/YouTube source/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Review clips/i })).toHaveAttribute('href', '/projects/3/editor')
  })

  it('uses one status-appropriate action for each project card', async () => {
    mockApi([
      { path: '/health', json: healthGemini },
      {
        path: '/projects',
        json: {
          projects: [
            project({ id: 1, title: 'Created project', status: 'created' }),
            project({ id: 2, title: 'Running project', status: 'running' }),
            project({ id: 3, title: 'Ready project', status: 'ready' }),
            project({ id: 4, title: 'Failed project', status: 'failed' }),
          ],
        },
      },
    ])

    renderApp('/')

    expect(await screen.findByRole('link', { name: /Start processing/i })).toHaveAttribute('href', '/projects/1')
    expect(screen.getByRole('link', { name: /View progress/i })).toHaveAttribute('href', '/projects/2')
    expect(screen.getByRole('link', { name: /Review clips/i })).toHaveAttribute('href', '/projects/3/editor')
    expect(screen.getByRole('link', { name: /Review status/i })).toHaveAttribute('href', '/projects/4')
  })

  it('exposes an accessible loading state', async () => {
    mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects', json: { projects: [] } },
    ])

    renderApp('/')

    expect(screen.getByLabelText('Loading')).toBeInTheDocument()
    await screen.findByText('No projects yet')
  })

  it('shows the dashboard empty state', async () => {
    mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects', json: { projects: [] } },
    ])

    renderApp('/')

    expect(await screen.findByText('No projects yet')).toBeInTheDocument()
  })

  it('shows the dashboard API error state', async () => {
    mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects', status: 500, json: { detail: 'database unavailable' } },
    ])

    renderApp('/')

    expect(await screen.findByText('Dashboard API error')).toBeInTheDocument()
    expect(screen.getByText('database unavailable')).toBeInTheDocument()
  })

  it('validates the new project form before submit', async () => {
    const user = userEvent.setup()
    mockApi([{ path: '/health', json: healthGemini }])

    renderApp('/projects/new')
    await user.click(screen.getByRole('button', { name: /^Create Project$/ }))

    expect(await screen.findByText('Enter a YouTube URL.')).toBeInTheDocument()
  })

  it('creates a project and navigates to it', async () => {
    const user = userEvent.setup()
    const api = mockApi([
      { path: '/health', json: healthGemini },
      { method: 'POST', path: '/projects', json: { project: project({ id: 9, title: 'New Show', status: 'created', current_stage: 'waiting', progress_percent: 0 }) } },
      { path: '/projects/9', json: { project: project({ id: 9, title: 'New Show', status: 'created', current_stage: 'waiting', progress_percent: 0 }) } },
      { path: '/projects/9/status', json: projectStatus({ project_id: 9, status: 'created', current_stage: 'waiting', stage: 'waiting', progress_percent: 0, message: 'Waiting to start', clip_count: 0 }) },
    ])

    renderApp('/projects/new')
    await user.type(screen.getByLabelText('YouTube URL'), 'https://www.youtube.com/watch?v=newshow')
    await user.type(screen.getByLabelText('Project title'), 'New Show')
    await user.click(screen.getByRole('button', { name: /^Create Project$/ }))

    expect(await screen.findByText('New Show')).toBeInTheDocument()
    expect(api.calls.find((call) => call.method === 'POST' && call.path === '/projects')?.body).toMatchObject({
      auto_start: false,
      auto_review: true,
      title: 'New Show',
    })
  })

  it('creates and starts processing when requested', async () => {
    const user = userEvent.setup()
    const api = mockApi([
      { path: '/health', json: healthGemini },
      { method: 'POST', path: '/projects', json: { project: project({ id: 10, title: 'Started Show', status: 'queued', progress_percent: 0 }) } },
      { path: '/projects/10', json: { project: project({ id: 10, title: 'Started Show', status: 'queued', progress_percent: 0 }) } },
      { path: '/projects/10/status', json: projectStatus({ project_id: 10, status: 'queued', current_stage: 'waiting', stage: 'waiting', progress_percent: 0, message: 'Waiting to start', clip_count: 0 }) },
    ])

    renderApp('/projects/new')
    await user.type(screen.getByLabelText('YouTube URL'), 'https://youtu.be/startnow')
    await user.click(screen.getByRole('button', { name: /Create and Start Processing/i }))

    await screen.findByText('Started Show')
    expect(api.calls.find((call) => call.method === 'POST' && call.path === '/projects')?.body).toMatchObject({
      auto_start: true,
    })
  })

  it('displays processing stages', async () => {
    mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects/3', json: { project: project({ status: 'running', current_stage: 'transcribing', progress_percent: 35 }) } },
      { path: '/projects/3/status', json: projectStatus({ status: 'running', current_stage: 'transcribing', stage: 'transcribing', progress_percent: 35, message: 'Transcribing podcast' }) },
    ])

    renderApp('/projects/3')

    expect(await screen.findByText('Transcribing podcast')).toBeInTheDocument()
    expect(screen.getByText('Generating candidates')).toBeInTheDocument()
  })

  it('shows only safe Airflow run metadata and links to the Airflow UI', async () => {
    mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects/3', json: { project: project({ status: 'running', current_stage: 'transcribing', progress_percent: 35 }) } },
      { path: '/projects/3/status', json: projectStatus({
        status: 'running',
        current_stage: 'transcribing',
        stage: 'transcribing',
        progress_percent: 35,
        message: 'Transcribing podcast',
        orchestrator_type: 'airflow',
        airflow_dag_run_id: 'project-3-job-12-20260717T120000Z',
        airflow_state: 'running',
        airflow_task_id: 'transcribe',
        airflow_ui_url: 'http://localhost:8080/dags/podcast_clip_pipeline/runs/project-3-job-12-20260717T120000Z',
        retry_attempt: 1,
        retry_max_attempts: 2,
      }) },
    ])

    renderApp('/projects/3')

    const details = (await screen.findByText('Technical details')).closest('details')
    expect(details).not.toHaveAttribute('open')
    fireEvent.click(screen.getByText('Technical details'))
    expect(screen.getByText('Airflow run')).toBeInTheDocument()
    expect(screen.getByText('transcribe')).toBeInTheDocument()
    expect(screen.getByText('1 of 2')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open in Airflow' })).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.queryByText(/password|workspace_relative_path|AIRFLOW_API_PASSWORD/i)).not.toBeInTheDocument()
  })

  it('shows only cancel and prevents duplicate cancel clicks while running', async () => {
    const runningStatus = projectStatus({ status: 'running', current_stage: 'reviewing_with_ai', stage: 'reviewing_with_ai', progress_percent: 89, message: 'Reviewing clip boundaries (2 of 5 complete)', clip_count: 5 })
    const cancelledStatus = projectStatus({ status: 'cancelled', current_stage: 'cancelled', stage: 'cancelled', progress_percent: 89, message: 'Cancelled', clip_count: 5 })
    const api = mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects/3', json: { project: project({ status: 'running', current_stage: 'reviewing_with_ai', progress_percent: 89, clip_count: 5 }) } },
      { path: '/projects/3/status', json: runningStatus },
      { method: 'POST', path: '/projects/3/cancel', json: cancelledStatus },
    ])
    const user = userEvent.setup()

    renderApp('/projects/3')

    const cancelButton = await screen.findByRole('button', { name: 'Cancel Processing' })
    expect(screen.queryByRole('button', { name: 'Start Processing' })).not.toBeInTheDocument()
    expect(cancelButton).toBeEnabled()
    await user.dblClick(cancelButton)
    await screen.findByText('Project cancelled.')
    expect(api.calls.filter((call) => call.method === 'POST' && call.path === '/projects/3/cancel')).toHaveLength(1)
  })

  it('shows Review Clips instead of Start Processing when ready', async () => {
    mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects/3', json: { project: project({ status: 'ready' }) } },
      { path: '/projects/3/status', json: projectStatus({ status: 'ready' }) },
    ])

    renderApp('/projects/3')

    expect(await screen.findByRole('link', { name: 'Review Clips' })).toHaveAttribute('href', '/projects/3/editor')
    expect(screen.getByRole('link', { name: 'View Exports' })).toHaveAttribute('href', '/projects/3/exports')
    expect(screen.queryByRole('button', { name: 'Start Processing' })).not.toBeInTheDocument()
    expect(screen.getByText('Completed processing stages').closest('details')).not.toHaveAttribute('open')
  })

  it('does not poll a created project before processing starts', async () => {
    const api = mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects/3', json: { project: project({ status: 'created', current_stage: 'waiting', progress_percent: 0 }) } },
      { path: '/projects/3/status', json: projectStatus({ status: 'created', current_stage: 'waiting', stage: 'waiting', progress_percent: 0 }) },
    ])

    renderApp('/projects/3')

    expect(await screen.findByRole('button', { name: 'Start Processing' })).toBeEnabled()
    await new Promise((resolve) => window.setTimeout(resolve, 80))
    expect(api.calls.filter((call) => call.path === '/projects/3/status')).toHaveLength(1)
  })

  it('cleans up active polling when leaving the processing page', async () => {
    const api = mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects/3', json: { project: project({ status: 'running', current_stage: 'transcribing', progress_percent: 35 }) } },
      { path: '/projects/3/status', json: projectStatus({ status: 'running', current_stage: 'transcribing', stage: 'transcribing', progress_percent: 35, message: 'Transcribing podcast' }) },
    ])

    const view = renderApp('/projects/3')
    await screen.findByText('Transcribing podcast')
    await waitFor(() => expect(api.calls.filter((call) => call.path === '/projects/3/status').length).toBeGreaterThan(1))
    view.unmount()
    const afterUnmount = api.calls.filter((call) => call.path === '/projects/3/status').length
    await new Promise((resolve) => window.setTimeout(resolve, 80))
    expect(api.calls.filter((call) => call.path === '/projects/3/status')).toHaveLength(afterUnmount)
  })

  it('shows persisted per-clip review progress', async () => {
    mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects/3', json: { project: project({ status: 'running', current_stage: 'reviewing_with_ai', progress_percent: 91, clip_count: 5 }) } },
      { path: '/projects/3/status', json: projectStatus({ status: 'running', current_stage: 'reviewing_with_ai', stage: 'reviewing_with_ai', progress_percent: 91, message: 'Reviewing clip boundaries (3 of 5 complete)', clip_count: 5 }) },
    ])

    renderApp('/projects/3')

    expect(await screen.findByText('Reviewing clip boundaries (3 of 5 complete)')).toBeInTheDocument()
  })

  it('shows review timeout failure without leaving action spinners active', async () => {
    mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects/3', json: { project: project({ status: 'failed', current_stage: 'failed', progress_percent: 91, error_message: 'Automatic boundary review exceeded its configured batch timeout.' }) } },
      { path: '/projects/3/status', json: projectStatus({ status: 'failed', current_stage: 'failed', stage: 'failed', progress_percent: 91, message: 'Failed', error_message: 'Automatic boundary review exceeded its configured batch timeout.' }) },
    ])

    const { container } = renderApp('/projects/3')

    expect(await screen.findByText('Automatic boundary review exceeded its configured batch timeout.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start Processing' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Cancel Processing' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry Processing' })).toBeEnabled()
    expect(container.querySelector('.animate-spin')).not.toBeInTheDocument()
  })

  it('stops polling when a project becomes ready', async () => {
    let statusCalls = 0
    const api = mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects/3', json: { project: project({ status: 'running', current_stage: 'transcribing', progress_percent: 35 }) } },
      {
        path: '/projects/3/status',
        json: () => {
          statusCalls += 1
          return statusCalls === 1
            ? projectStatus({ status: 'running', current_stage: 'transcribing', stage: 'transcribing', progress_percent: 35, message: 'Transcribing podcast' })
            : projectStatus({ status: 'ready', current_stage: 'ready', stage: 'ready', progress_percent: 100, message: 'Ready for review' })
        },
      },
    ])

    renderApp('/projects/3')

    await screen.findByText('Transcribing podcast')
    await waitFor(() => expect(screen.getByText('Ready for review')).toBeInTheDocument())
    const afterReady = api.calls.filter((call) => call.path === '/projects/3/status').length
    await new Promise((resolve) => window.setTimeout(resolve, 80))
    expect(api.calls.filter((call) => call.path === '/projects/3/status')).toHaveLength(afterReady)
  })

  it('stops polling when a project fails', async () => {
    let statusCalls = 0
    const api = mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects/3', json: { project: project({ status: 'running', current_stage: 'downloading', progress_percent: 15 }) } },
      {
        path: '/projects/3/status',
        json: () => {
          statusCalls += 1
          return statusCalls === 1
            ? projectStatus({ status: 'running', current_stage: 'downloading', stage: 'downloading', progress_percent: 15, message: 'Downloading source media' })
            : projectStatus({ status: 'failed', current_stage: 'failed', stage: 'failed', progress_percent: 0, message: 'Failed', error_message: 'download failed' })
        },
      },
    ])

    renderApp('/projects/3')

    await screen.findByText('Downloading source media')
    await waitFor(() => expect(screen.getByText('download failed')).toBeInTheDocument())
    const afterFailed = api.calls.filter((call) => call.path === '/projects/3/status').length
    await new Promise((resolve) => window.setTimeout(resolve, 80))
    expect(api.calls.filter((call) => call.path === '/projects/3/status')).toHaveLength(afterFailed)
  })

  it('loads clips from the project-specific endpoint', async () => {
    const api = mockApi(editorRoutes([clip()]))

    renderApp('/projects/3/editor')

    expect(await screen.findByText('Clip 1')).toBeInTheDocument()
    expect(api.calls.some((call) => call.path === '/projects/3/clips')).toBe(true)
    expect(api.calls.some((call) => call.path === '/clips')).toBe(false)
  })

  it('does not show stale clips after switching projects', async () => {
    function SwitchHarness() {
      const navigate = useNavigate()
      return (
        <>
          <button type="button" onClick={() => navigate('/projects/4/editor')}>Switch to Project 4</button>
          <AppRoutes />
        </>
      )
    }
    mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects/3', json: { project: project({ id: 3, title: 'Project Three' }) } },
      { path: '/projects/3/clips', json: { clips: [clip({ id: 'clip_003', index: 3, summary: 'Only project three clip' })] } },
      { path: '/projects/4', json: { project: project({ id: 4, title: 'Project Four' }) } },
      { path: '/projects/4/clips', json: { clips: [clip({ id: 'clip_004', project_id: 4, index: 4, summary: 'Only project four clip' })] } },
    ])

    render(
      <MemoryRouter initialEntries={['/projects/3/editor']}>
        <SwitchHarness />
      </MemoryRouter>,
    )

    expect(await screen.findByText('Only project three clip')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Switch to Project 4' }))
    expect(await screen.findByText('Only project four clip')).toBeInTheDocument()
    expect(screen.queryByText('Only project three clip')).not.toBeInTheDocument()
  })

  it('selects clips from the clip list', async () => {
    const secondClip = clip({ id: 'clip_002', index: 2, ai_start: 20, ai_end: 29, reviewed_start: null, reviewed_end: null, edited_start: 20, edited_end: 29, summary: 'Second clip excerpt' })
    mockApi(editorRoutes([clip(), secondClip]))

    renderApp('/projects/3/editor')
    await screen.findByText('Second clip excerpt')
    fireEvent.click(screen.getByRole('button', { name: /Clip 2/i }))

    expect(screen.getByText('Clip interval 20.0s to 29.0s')).toBeInTheDocument()
  })

  it('selecting a clip seeks to edited_start', async () => {
    const secondClip = clip({ id: 'clip_002', index: 2, ai_start: 20, ai_end: 29, reviewed_start: 21, reviewed_end: 29, edited_start: 22.5, edited_end: 29, summary: 'Second clip excerpt' })
    mockApi(editorRoutes([clip(), secondClip]))

    const { container } = renderApp('/projects/3/editor')
    await screen.findByText('Second clip excerpt')
    const video = getRenderedVideo(container)
    markVideoReady(video)

    fireEvent.click(screen.getByRole('button', { name: /Clip 2/i }))

    expect(video.currentTime).toBe(22.5)
    expect(screen.getByText('Current time')).toBeInTheDocument()
    expect(screen.getAllByText('22.5s').length).toBeGreaterThan(0)
  })

  it('uses fallback seek order from edited to reviewed to original', async () => {
    const reviewedFallback = clip({
      id: 'clip_002',
      index: 2,
      ai_start: 20,
      ai_end: 28,
      reviewed_start: 21.5,
      reviewed_end: 28,
      edited_start: undefined as unknown as number,
      edited_end: undefined as unknown as number,
      summary: 'Reviewed fallback clip',
    })
    const originalFallback = clip({
      id: 'clip_003',
      index: 3,
      ai_start: 40,
      ai_end: 48,
      reviewed_start: null,
      reviewed_end: null,
      edited_start: undefined as unknown as number,
      edited_end: undefined as unknown as number,
      summary: 'Original fallback clip',
    })
    mockApi(editorRoutes([clip(), reviewedFallback, originalFallback]))

    const { container } = renderApp('/projects/3/editor')
    await screen.findByText('Reviewed fallback clip')
    const video = getRenderedVideo(container)
    markVideoReady(video)

    fireEvent.click(screen.getByRole('button', { name: /Clip 2/i }))
    expect(video.currentTime).toBe(21.5)

    fireEvent.click(screen.getByRole('button', { name: /Clip 3/i }))
    expect(video.currentTime).toBe(40)
  })

  it('applies pending seek after video metadata loads', async () => {
    mockApi(editorRoutes([clip({ edited_start: 15.25, edited_end: 24 })]))

    const { container } = renderApp('/projects/3/editor')
    await screen.findByText('Clip 1')
    const video = getRenderedVideo(container)
    expect(video.currentTime).toBe(0)

    markVideoReady(video)
    fireEvent.loadedMetadata(video)

    expect(video.currentTime).toBe(15.25)
    expect(screen.getAllByText('15.3s').length).toBeGreaterThan(0)
  })

  it('clip selection does not autoplay', async () => {
    const secondClip = clip({ id: 'clip_002', index: 2, edited_start: 30, edited_end: 38, summary: 'Second clip excerpt' })
    mockApi(editorRoutes([clip(), secondClip]))

    const { container } = renderApp('/projects/3/editor')
    await screen.findByText('Second clip excerpt')
    markVideoReady(getRenderedVideo(container))
    fireEvent.click(screen.getByRole('button', { name: /Clip 2/i }))

    expect(HTMLMediaElement.prototype.play).not.toHaveBeenCalled()
  })

  it('normal playback is not repeatedly reset after selection', async () => {
    const secondClip = clip({ id: 'clip_002', index: 2, edited_start: 30, edited_end: 38, summary: 'Second clip excerpt' })
    mockApi(editorRoutes([clip(), secondClip]))

    const { container } = renderApp('/projects/3/editor')
    await screen.findByText('Second clip excerpt')
    const video = getRenderedVideo(container)
    markVideoReady(video)
    fireEvent.click(screen.getByRole('button', { name: /Clip 2/i }))
    expect(video.currentTime).toBe(30)

    video.currentTime = 34
    fireEvent.timeUpdate(video)

    expect(video.currentTime).toBe(34)
    expect(screen.getByText('34.0s')).toBeInTheDocument()
  })

  it('Preview selection uses the latest edited_start', async () => {
    mockApi(editorRoutes([clip()]))

    const { container } = renderApp('/projects/3/editor')
    const startInput = await screen.findByLabelText('Numeric start input')
    const video = getRenderedVideo(container)
    markVideoReady(video)

    fireEvent.change(startInput, { target: { value: '12.2' } })
    fireEvent.click(screen.getByRole('button', { name: /Preview selection/i }))

    expect(video.currentTime).toBe(12.2)
  })

  it('displays original, Gemini, and current boundaries', async () => {
    mockApi(editorRoutes([clip()]))

    renderApp('/projects/3/editor')

    expect(await screen.findByText('Original selection')).toBeInTheDocument()
    expect(screen.getByText('Gemini suggestion')).toBeInTheDocument()
    expect(screen.getByText('Current edit')).toBeInTheDocument()
    expect(screen.getAllByText('10.5s').length).toBeGreaterThan(0)
    expect(screen.getByLabelText('Start range control')).toBeInTheDocument()
    expect(screen.getByLabelText('End range control')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Preview selection' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reset to Gemini suggestion' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reset to original selection' })).toBeInTheDocument()
  })

  it('saves manual boundary edits to edited boundaries only', async () => {
    const api = mockApi([
      ...editorRoutes([clip()]),
      {
        method: 'PATCH',
        path: '/projects/3/clips/clip_001',
        json: { clip: clip({ edited_start: 11, edited_end: 18.5, boundary_source: 'user' }) },
      },
    ])

    renderApp('/projects/3/editor')
    const startInput = await screen.findByLabelText('Numeric start input')
    fireEvent.change(startInput, { target: { value: '11' } })
    fireEvent.click(screen.getByRole('button', { name: /Save Boundaries/i }))

    await screen.findByText('Edited boundaries saved.')
    expect(api.calls.find((call) => call.method === 'PATCH')?.body).toMatchObject({ start: 11, end: 18.5 })
    expect(screen.getByText('Original selection')).toBeInTheDocument()
  })

  it('Accept persists and keeps the accepted status visible', async () => {
    mockApi([
      ...editorRoutes([clip()]),
      { method: 'POST', path: '/projects/3/clips/clip_001/accept', json: { clip: clip({ status: 'accepted' }) } },
    ])

    renderApp('/projects/3/editor')
    await screen.findByText('Clip 1')
    fireEvent.click(screen.getByRole('button', { name: /^Accept Clip$/ }))

    expect(await screen.findByText('Clip accepted.')).toBeInTheDocument()
    expect(screen.getAllByText('Accepted').length).toBeGreaterThan(0)
  })

  it('uses Render Short as the primary action for an accepted clip', async () => {
    mockApi(editorRoutes([clip({ status: 'accepted', render_status: 'not_rendered' })]))

    renderApp('/projects/3/editor')

    expect(await screen.findByRole('button', { name: 'Render Short' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Accept Clip' })).not.toBeInTheDocument()
  })

  it('uses View Export as the primary action for a rendered clip', async () => {
    mockApi(editorRoutes([clip({ status: 'accepted', render_status: 'completed' })]))

    renderApp('/projects/3/editor')

    expect(await screen.findByRole('link', { name: 'View Export' })).toHaveAttribute('href', '/projects/3/exports')
    expect(screen.getByRole('button', { name: 'Re-render Short' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Accept Clip' })).not.toBeInTheDocument()
  })

  it('Reject persists and keeps the rejected status visible', async () => {
    mockApi([
      ...editorRoutes([clip()]),
      { method: 'POST', path: '/projects/3/clips/clip_001/reject', json: { clip: clip({ status: 'rejected' }) } },
    ])

    renderApp('/projects/3/editor')
    await screen.findByText('Clip 1')
    fireEvent.click(screen.getByRole('button', { name: /^Reject Clip$/ }))

    expect(await screen.findByText('Clip rejected.')).toBeInTheDocument()
    expect(screen.getAllByText('Rejected').length).toBeGreaterThan(0)
    expect(screen.getByText('This clip is rejected. Accept it again before rendering.')).toBeInTheDocument()
  })

  it('shows the configured reviewer label', async () => {
    mockApi(editorRoutes([clip()], healthGemini))

    renderApp('/projects/3/editor')

    expect(await screen.findByText('Gemini / gemini-3.5-flash')).toBeInTheDocument()
  })

  it('shows the historical reviewer label from the clip', async () => {
    mockApi(editorRoutes([clip({ latest_review_provider: 'local_stub', latest_review_model: 'local-stub' })], healthGemini))

    renderApp('/projects/3/editor')

    expect((await screen.findAllByText('Local stub')).length).toBeGreaterThan(0)
  })

  it('requires confirmation before re-running a paid AI review', async () => {
    const user = userEvent.setup()
    const api = mockApi([
      ...editorRoutes([clip()]),
      { method: 'POST', path: '/projects/3/clips/clip_001/review', json: { provider: 'gemini', decision: 'render_ready' } },
    ])

    renderApp('/projects/3/editor')

    await user.click(await screen.findByRole('button', { name: 'Re-run AI Review for This Clip' }))
    expect(screen.getByRole('dialog', { name: 'Replace the saved AI review?' })).toBeInTheDocument()
    expect(screen.getByText(/may incur API cost/i)).toBeInTheDocument()
    expect(api.calls.filter((call) => call.method === 'POST' && call.path.endsWith('/review'))).toHaveLength(0)

    await user.click(screen.getByRole('button', { name: 'Run Review' }))

    expect(await screen.findByText('Review saved for selected clip.')).toBeInTheDocument()
    expect(api.calls.filter((call) => call.method === 'POST' && call.path.endsWith('/review'))).toHaveLength(1)
  })

  it('shows manual-review clip state', async () => {
    mockApi(editorRoutes([clip({
      latest_review_decision: 'manual_review',
      latest_review_failed: true,
      latest_review_failure_category: 'boundary_validation',
      latest_review_warnings: ['End must stay within +/-20s of AI end. Adjusted duration must not exceed 90 seconds.'],
    })], healthLocal))

    renderApp('/projects/3/editor')

    expect(await screen.findByText('Needs manual review')).toBeInTheDocument()
    expect(screen.getByText('Gemini returned boundaries outside the permitted clip range. This clip requires manual review.')).toBeInTheDocument()
    const technicalDetails = screen.getByText('Technical details').closest('details')
    expect(technicalDetails).not.toHaveAttribute('open')
    expect(screen.getByText('End must stay within +/-20s of AI end. Adjusted duration must not exceed 90 seconds.')).toBeInTheDocument()
  })

  it('shows render success', async () => {
    mockApi([
      ...editorRoutes([clip({ status: 'accepted' })]),
      { method: 'POST', path: '/projects/3/render', json: { status: 'completed', clip_id: 'clip_001', start: 10.5, end: 18.5, duration: 8, warnings: [], clip: clip({ status: 'accepted', render_status: 'completed' }) } },
    ])

    renderApp('/projects/3/editor')
    await screen.findByText('Clip 1')
    fireEvent.click(screen.getByRole('button', { name: /Render Short/i }))

    expect(await screen.findByText('Render completed.')).toBeInTheDocument()
  })

  it('shows render errors', async () => {
    mockApi([
      ...editorRoutes([clip({ status: 'accepted' })]),
      { method: 'POST', path: '/projects/3/render', status: 400, json: { detail: { message: 'Missing source video.' } } },
    ])

    renderApp('/projects/3/editor')
    await screen.findByText('Clip 1')
    fireEvent.click(screen.getByRole('button', { name: /Render Short/i }))

    expect(await screen.findByText('Missing source video.')).toBeInTheDocument()
  })

  it('keeps technical logs collapsed by default', async () => {
    mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects/3', json: { project: project({ status: 'ready' }) } },
      { path: '/projects/3/status', json: projectStatus({ status: 'ready', message: 'Ready for review' }) },
      { path: '/projects/3/logs?tail=200', json: { project_id: 3, tail: 200, lines: ['SECRET LOG LINE'] } },
    ])

    renderApp('/projects/3')

    expect(await screen.findByText('Technical details')).toBeInTheDocument()
    expect(screen.queryByText('SECRET LOG LINE')).not.toBeInTheDocument()
  })

  it('failed projects use error presentation', async () => {
    mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects', json: { projects: [project({ status: 'failed', current_stage: 'downloading', progress_percent: 100, error_message: 'Download failed' })] } },
    ])

    const { container } = renderApp('/')

    expect(await screen.findByText(/Download failed/)).toBeInTheDocument()
    const progressBar = screen.getByRole('progressbar')
    expect(progressBar).toHaveAttribute('aria-valuenow', '95')
    const progressFill = container.querySelector('[role="progressbar"] > div')
    expect(progressFill).toHaveClass('bg-app-danger')
  })

  it('shows exports empty state', async () => {
    mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects/3', json: { project: project() } },
      { path: '/projects/3/exports', json: { exports: [] } },
    ])

    renderApp('/projects/3/exports')

    expect(await screen.findByText('No rendered clips yet')).toBeInTheDocument()
  })

  it('shows rendered exports and download actions', async () => {
    mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects/3', json: { project: project() } },
      { path: '/projects/3/exports', json: { exports: [exportItem()] } },
    ])

    renderApp('/projects/3/exports')

    expect(await screen.findByText('Clip 1')).toBeInTheDocument()
    expect(screen.getByText('With subtitles')).toBeInTheDocument()
    expect(screen.getByText('segment_001.mp4')).toBeInTheDocument()
    const link = screen.getByRole('link', { name: 'Download With subtitles' })
    expect(link).toHaveAttribute('href', '/projects/3/exports/501/download')
  })

  it('groups raw and subtitled exports under one clip', async () => {
    mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects/3', json: { project: project() } },
      {
        path: '/projects/3/exports',
        json: {
          exports: [
            exportItem({ id: 501, clip_id: 'clip_002', clip_index: 2, artifact_type: 'subtitled_clip', filename: 'segment_002_subs.mp4', download_url: '/projects/3/exports/501/download', preview_url: '/projects/3/exports/501/download' }),
            exportItem({ id: 502, clip_id: 'clip_002', clip_index: 2, artifact_type: 'raw_clip', filename: 'segment_002_raw.mp4', download_url: '/projects/3/exports/502/download', preview_url: '/projects/3/exports/502/download' }),
          ],
        },
      },
    ])

    renderApp('/projects/3/exports')

    expect(await screen.findByText('Clip 2')).toBeInTheDocument()
    const subtitledTab = screen.getByRole('button', { name: /With subtitles.*recommended/i })
    const visibleLabel = within(subtitledTab).getByText('With subtitles')
    const recommendedBadge = within(subtitledTab).getByText('Recommended')
    expect(visibleLabel).not.toBe(recommendedBadge)
    expect(recommendedBadge).toHaveClass('rounded-full')
    expect(subtitledTab).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: 'Raw' }))
    expect(screen.getByRole('link', { name: 'Download Raw' })).toHaveAttribute('href', '/projects/3/exports/502/download')
  })

  it('shows the latest render by default and collapses previous renders', async () => {
    mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects/3', json: { project: project() } },
      {
        path: '/projects/3/exports',
        json: {
          exports: [
            exportItem({ id: 504, artifact_type: 'subtitled_clip', created_at: '2026-07-16T12:15:00Z' }),
            exportItem({ id: 503, artifact_type: 'raw_clip', created_at: '2026-07-16T12:14:58Z' }),
            exportItem({ id: 502, artifact_type: 'subtitled_clip', created_at: '2026-07-16T10:00:00Z' }),
            exportItem({ id: 501, artifact_type: 'raw_clip', created_at: '2026-07-16T09:59:58Z' }),
          ],
        },
      },
    ])

    const { container } = renderApp('/projects/3/exports')

    expect(await screen.findByRole('button', { name: /With subtitles.*recommended/i })).toHaveAttribute('aria-pressed', 'true')
    const history = screen.getByText('Previous renders (1)').closest('details')
    expect(history).not.toHaveAttribute('open')
    expect(container.querySelectorAll('video')).toHaveLength(1)
  })

  it('does not label an existing raw export as Not rendered', async () => {
    mockApi([
      { path: '/health', json: healthGemini },
      { path: '/projects/3', json: { project: project() } },
      { path: '/projects/3/exports', json: { exports: [exportItem({ artifact_type: 'raw_clip', filename: 'segment_001_raw.mp4' })] } },
    ])

    renderApp('/projects/3/exports')

    expect(await screen.findByRole('button', { name: 'Raw' })).toBeInTheDocument()
    expect(screen.queryByText('Not rendered')).not.toBeInTheDocument()
  })

  it('shows backend unavailable state', async () => {
    mockApi([
      { path: '/health', status: 503, json: { detail: 'api offline' } },
      { path: '/projects', status: 503, json: { detail: 'api offline' } },
    ])

    renderApp('/')

    expect(await screen.findByText('Dashboard API error')).toBeInTheDocument()
    const trigger = screen.getByRole('button', { name: 'System issue' })
    fireEvent.click(trigger)
    expect(screen.getByText('Unavailable')).toBeInTheDocument()
  })

  it('New Project shows one review control', async () => {
    mockApi([{ path: '/health', json: healthGemini }])

    renderApp('/projects/new')

    expect(await screen.findByText('Automatic Gemini review')).toBeInTheDocument()
    expect(screen.getByText('Gemini will review candidate clips and improve their start and end boundaries.')).toBeInTheDocument()
    expect(screen.getAllByRole('checkbox')).toHaveLength(1)
  })

  it('does not include frontend secrets or secret environment examples', () => {
    const root = process.cwd()
    const files = collectTextFiles(root).filter((file) => !file.includes(`${join('node_modules')}`) && !file.includes(`${join('dist')}`))
    const combined = files.map((file) => readFileSync(file, 'utf8')).join('\n')

    expect(combined).not.toMatch(/GEMINI_API_KEY/)
    expect(combined).not.toMatch(/VITE_GEMINI/)
    expect(combined).not.toMatch(/CLIP_REVIEW_MODE/)
    expect(combined).not.toMatch(/PODCAST_CUTTER_DB_URL/)
    expect(readFileSync(join(root, '.env.example'), 'utf8').trim()).toBe('VITE_API_BASE_URL=http://127.0.0.1:8010')
  })
})

function collectTextFiles(root: string): string[] {
  const entries = readdirSync(root)
  const files: string[] = []
  for (const entry of entries) {
    const path = join(root, entry)
    const stats = statSync(path)
    if (stats.isDirectory()) {
      if (entry === 'node_modules' || entry === 'dist' || entry === 'coverage') {
        continue
      }
      files.push(...collectTextFiles(path))
      continue
    }
    if ((/\.(ts|tsx|css|html|js|json|md|example)$/.test(entry) || entry === '.env.example') && !entry.endsWith('.test.tsx')) {
      files.push(path)
    }
  }
  return files
}
