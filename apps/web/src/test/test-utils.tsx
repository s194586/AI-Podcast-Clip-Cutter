import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'
import { AppRoutes } from '../App'

export interface FetchCall {
  method: string
  path: string
  search: string
  body: unknown
}

export interface MockRoute {
  method?: string
  path: string | RegExp
  status?: number
  json?: unknown | ((call: FetchCall) => unknown)
}

export function renderApp(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>,
  )
}

export function mockApi(routes: MockRoute[]) {
  const calls: FetchCall[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const rawUrl = typeof input === 'string' || input instanceof URL ? String(input) : input.url
    const url = new URL(rawUrl, 'http://localhost')
    const method = String(init?.method ?? 'GET').toUpperCase()
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
    const call = { method, path: url.pathname, search: url.search, body }
    calls.push(call)
    const route = routes.find((candidate) => {
      const routeMethod = String(candidate.method ?? 'GET').toUpperCase()
      const pathMatches = typeof candidate.path === 'string'
        ? candidate.path === `${url.pathname}${url.search}` || candidate.path === url.pathname
        : candidate.path.test(`${url.pathname}${url.search}`)
      return routeMethod === method && pathMatches
    })
    if (!route) {
      return jsonResponse({ detail: `Unhandled ${method} ${url.pathname}${url.search}` }, 404)
    }
    const payload = typeof route.json === 'function' ? route.json(call) : route.json
    return jsonResponse(payload ?? {}, route.status ?? 200)
  })
  vi.stubGlobal('fetch', fetchMock)
  return { calls, fetchMock }
}

function jsonResponse(payload: unknown, status: number) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      'Content-Type': 'application/json',
    },
  })
}
