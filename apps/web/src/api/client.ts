import { ApiError, messageFromDetail } from './errors'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export interface ApiRequestOptions extends RequestInit {
  json?: unknown
}

export function apiUrl(path: string): string {
  const normalized = path.startsWith('/') ? path : `/${path}`
  return `${API_BASE_URL}${normalized}`
}

async function parseJson(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) {
    return null
  }
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

async function parseError(response: Response): Promise<ApiError> {
  const detail = await parseJson(response)
  const message = messageFromDetail(detail, response.statusText || `HTTP ${response.status}`)
  return new ApiError(message, response.status, detail)
}

export async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const headers = new Headers(options.headers)
  const init: RequestInit = {
    ...options,
    headers,
  }
  if (options.json !== undefined) {
    headers.set('Content-Type', 'application/json')
    init.body = JSON.stringify(options.json)
  }
  delete (init as ApiRequestOptions).json

  const response = await fetch(apiUrl(path), init)
  if (!response.ok) {
    throw await parseError(response)
  }
  return (await parseJson(response)) as T
}

export async function apiBlob(path: string, options: ApiRequestOptions = {}): Promise<Blob> {
  const response = await fetch(apiUrl(path), options)
  if (!response.ok) {
    throw await parseError(response)
  }
  return response.blob()
}
