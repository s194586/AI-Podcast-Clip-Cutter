export type StageName =
  | 'waiting'
  | 'downloading'
  | 'transcribing'
  | 'validating_transcript'
  | 'generating_candidates'
  | 'importing_candidates'
  | 'reviewing_with_ai'
  | 'ready'
  | 'failed'
  | 'cancelled'
  | string

export type ProjectRunStatus = 'created' | 'queued' | 'running' | 'ready' | 'failed' | 'cancelled' | string

export interface Project {
  id: number
  title: string | null
  source_url: string
  status: ProjectRunStatus
  current_stage?: StageName
  stage?: StageName
  progress_percent: number
  error_message?: string | null
  auto_review: boolean
  created_at?: string | null
  started_at?: string | null
  completed_at?: string | null
  updated_at?: string | null
  clip_count?: number
  accepted_clip_count?: number
}

export interface JobStatus {
  id: number
  job_type: string
  status: string
  stage?: string | null
  progress_percent?: number
  started_at?: string | null
  finished_at?: string | null
  exit_code?: number | null
  error_message?: string | null
  orchestrator_type?: string | null
  airflow_dag_id?: string | null
  airflow_dag_run_id?: string | null
  airflow_state?: string | null
  airflow_ui_url?: string | null
  airflow_task_id?: string | null
  retry_attempt?: number | null
  retry_max_attempts?: number | null
}

export interface ProjectStatus {
  project_id: number
  status: ProjectRunStatus
  stage: StageName
  current_stage: StageName
  progress_percent: number
  message: string
  error_message?: string | null
  started_at?: string | null
  updated_at?: string | null
  completed_at?: string | null
  clip_count: number
  last_error?: string | null
  job?: JobStatus | null
  orchestrator_type?: string | null
  airflow_dag_id?: string | null
  airflow_dag_run_id?: string | null
  airflow_state?: string | null
  airflow_ui_url?: string | null
  airflow_task_id?: string | null
  retry_attempt?: number | null
  retry_max_attempts?: number | null
}

export interface ProjectLogTail {
  project_id: number
  tail: number
  lines: string[]
}

export interface Clip {
  id: string
  database_id: number
  project_id: number
  index: number
  ai_start: number
  ai_end: number
  reviewed_start: number | null
  reviewed_end: number | null
  edited_start: number
  edited_end: number
  boundary_source: string
  min_start: number
  max_start: number
  min_end: number
  max_end: number
  duration: number
  summary: string
  text: string
  source?: string | null
  status: string
  candidate_id?: string | null
  selection_source?: string | null
  local_score?: number | null
  local_rank?: number | null
  selection_reasons?: string[]
  render_status: string
  raw_outputs?: string[]
  subtitled_outputs?: string[]
  last_render_warnings?: string[]
  latest_review_provider?: string | null
  latest_review_model?: string | null
  latest_review_decision?: string | null
  latest_review_recommended_action?: string | null
  latest_review_reasoning_summary?: string
  latest_review_start_reason?: string
  latest_review_end_reason?: string
  latest_review_warnings?: string[]
  latest_review_failed?: boolean
  latest_review_failure_category?: string | null
  latest_review_changed_boundaries?: boolean
  created_at?: string | null
  updated_at?: string | null
}

export interface HealthStatus {
  status: string
  pipeline_orchestrator?: string
  clip_review_provider?: string | null
  clip_review_model?: string | null
  clip_review_mode_source?: string | null
  gemini_api_key_configured?: boolean
  review_config?: {
    provider?: string | null
    model?: string | null
    mode_source?: string | null
    gemini_api_key_configured?: boolean
    warnings?: string[]
  }
}

export interface CreateProjectPayload {
  source_url: string
  title?: string | null
  auto_review: boolean
  auto_start: boolean
}

export interface CreateProjectResponse {
  project: Project
  job?: JobStatus
  status?: ProjectStatus
}

export interface ClipResponse {
  clip: Clip
}

export interface ClipsResponse {
  clips: Clip[]
}

export interface ProjectsResponse {
  projects: Project[]
}

export interface ProjectResponse {
  project: Project
}

export interface RenderResult {
  status: string
  clip_id: string
  start: number
  end: number
  duration: number
  warnings: string[]
  clip: Clip
}

export interface ReviewResult {
  provider?: string
  model?: string | null
  clip_id?: string
  project_id?: number
  decision?: string
  recommended_action?: string
  reasoning_summary?: string
  reviewed_start?: number | null
  reviewed_end?: number | null
  clip?: Clip
  [key: string]: unknown
}

export interface ProjectReviewResult {
  provider?: string
  project_id?: number
  clip_count?: number
  reviewed_count?: number
  failed_count?: number
  [key: string]: unknown
}

export interface ExportItem {
  id: number
  project_id: number
  clip_id: string | null
  clip_database_id: number | null
  clip_index: number | null
  artifact_type: string
  filename: string
  media_type: string
  created_at: string | null
  duration: number | null
  file_size: number
  download_url: string
  preview_url: string
}

export interface ExportsResponse {
  exports: ExportItem[]
}
