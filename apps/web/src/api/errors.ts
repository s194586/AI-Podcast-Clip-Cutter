export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(message: string, status: number, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export function messageFromDetail(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') {
    return detail
  }
  if (detail && typeof detail === 'object') {
    const value = detail as Record<string, unknown>
    if (typeof value.message === 'string') {
      return value.message
    }
    if (value.detail !== undefined) {
      return messageFromDetail(value.detail, fallback)
    }
  }
  return fallback
}

export function getErrorMessage(error: unknown, fallback = 'Request failed.'): string {
  if (error instanceof ApiError) {
    return error.message
  }
  if (error instanceof DOMException && error.name === 'AbortError') {
    return 'Request cancelled.'
  }
  if (error instanceof Error) {
    return error.message
  }
  return fallback
}
