import { apiRequest } from './client'
import type { HealthStatus } from './types'

export function getHealth(signal?: AbortSignal): Promise<HealthStatus> {
  return apiRequest<HealthStatus>('/health', { signal })
}
