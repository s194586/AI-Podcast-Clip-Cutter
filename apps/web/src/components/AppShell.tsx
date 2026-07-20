import { Activity, ChevronDown, LayoutDashboard, Menu, Plus, RefreshCcw, Scissors, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { getHealth } from '../api/health'
import type { HealthStatus } from '../api/types'
import { reviewerReadyLabel } from '../utils/format'
import { StatusBadge } from './StatusBadge'

export interface AppShellContext {
  health: HealthStatus | null
  healthError: string | null
  refreshHealth: () => void
}

export function AppShell() {
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [healthError, setHealthError] = useState<string | null>(null)
  const [navOpen, setNavOpen] = useState(false)
  const [statusOpen, setStatusOpen] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)
  const statusPopoverRef = useRef<HTMLDivElement>(null)
  const statusTriggerRef = useRef<HTMLButtonElement>(null)
  const location = useLocation()

  const refreshHealth = useCallback(() => setRefreshKey((value) => value + 1), [])

  useEffect(() => {
    const controller = new AbortController()
    getHealth(controller.signal)
      .then((value) => {
        setHealth(value)
        setHealthError(null)
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') {
          return
        }
        setHealth(null)
        setHealthError(error instanceof Error ? error.message : 'Backend unavailable.')
      })
    return () => controller.abort()
  }, [refreshKey])

  useEffect(() => {
    setStatusOpen(false)
  }, [location.pathname])

  useEffect(() => {
    if (!statusOpen) {
      return
    }

    const closeOnOutsidePointer = (event: PointerEvent) => {
      if (!statusPopoverRef.current?.contains(event.target as Node)) {
        setStatusOpen(false)
      }
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') {
        return
      }
      event.preventDefault()
      setStatusOpen(false)
      statusTriggerRef.current?.focus()
    }

    document.addEventListener('pointerdown', closeOnOutsidePointer)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsidePointer)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [statusOpen])

  return (
    <div className="min-h-screen bg-app-bg text-app-text">
      <header className="sticky top-0 z-20 border-b border-app-border bg-app-bg/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1680px] items-center gap-4 px-4 py-3 lg:px-6">
          <NavLink to="/" className="flex min-w-0 items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-md border border-app-accent/50 bg-app-accent/15 text-app-accent">
              <Scissors className="h-5 w-5" aria-hidden="true" />
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-semibold text-app-text">Podcast Shorts Cutter</span>
              <span className="block text-xs text-app-muted">Turn long conversations into finished shorts</span>
            </span>
          </NavLink>

          <button type="button" className="app-icon-button ml-auto lg:hidden" aria-label="Toggle navigation" onClick={() => setNavOpen((value) => !value)}>
            {navOpen ? <X className="h-4 w-4" aria-hidden="true" /> : <Menu className="h-4 w-4" aria-hidden="true" />}
          </button>

          <nav className={`${navOpen ? 'flex' : 'hidden'} absolute left-0 right-0 top-[65px] flex-col gap-2 border-b border-app-border bg-app-bg p-4 lg:static lg:ml-4 lg:flex lg:flex-1 lg:flex-row lg:items-center lg:border-0 lg:bg-transparent lg:p-0`}>
            <NavLink to="/" className={({ isActive }) => `app-button justify-start ${isActive ? 'border-app-accent text-app-text' : ''}`} onClick={() => setNavOpen(false)}>
              <LayoutDashboard className="h-4 w-4" aria-hidden="true" />
              Projects
            </NavLink>
            <NavLink to="/projects/new" className="app-button app-button-primary justify-start" onClick={() => setNavOpen(false)}>
              <Plus className="h-4 w-4" aria-hidden="true" />
              New Project
            </NavLink>
          </nav>

          <div ref={statusPopoverRef} className="relative hidden lg:block">
            <button
              ref={statusTriggerRef}
              type="button"
              className="app-button"
              aria-expanded={statusOpen}
              aria-controls="system-status-panel"
              onClick={() => setStatusOpen((value) => !value)}
            >
              <span className={`h-2 w-2 rounded-full ${healthError ? 'bg-app-danger' : health ? 'bg-app-accent' : 'bg-app-warning'}`} aria-hidden="true" />
              {healthError ? 'System issue' : health ? 'System ready' : 'Checking system'}
              <ChevronDown className={`h-4 w-4 transition ${statusOpen ? 'rotate-180' : ''}`} aria-hidden="true" />
            </button>
            {statusOpen ? (
              <div
                id="system-status-panel"
                role="region"
                aria-labelledby="system-status-heading"
                className="absolute right-0 top-12 z-30 w-72 rounded-panel border border-app-border bg-app-panel p-4 shadow-panel"
              >
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p id="system-status-heading" className="text-sm font-semibold text-app-text">Technical status</p>
                    <p className="mt-1 text-xs text-app-muted">Runtime and review-provider availability</p>
                  </div>
                  <StatusBadge
                    value={healthError ? 'failed' : health ? 'ready' : 'queued'}
                    tone={healthError ? 'danger' : health ? 'success' : 'neutral'}
                  />
                </div>
                <dl className="mt-4 grid gap-3 text-sm">
                  <div className="flex items-center justify-between gap-3">
                    <dt className="inline-flex items-center gap-2 text-app-muted">
                      <Activity className="h-4 w-4" aria-hidden="true" />
                      Backend
                    </dt>
                    <dd className="text-app-text">{healthError ? 'Unavailable' : health?.status ?? 'Checking'}</dd>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <dt className="text-app-muted">AI review</dt>
                    <dd className="text-right text-app-text">{reviewerReadyLabel(health)}</dd>
                  </div>
                </dl>
                {healthError ? <p className="mt-3 break-words text-xs leading-5 text-red-200">{healthError}</p> : null}
                <button type="button" className="app-button mt-4 w-full" onClick={refreshHealth}>
                  <RefreshCcw className="h-4 w-4" aria-hidden="true" />
                  Refresh status
                </button>
              </div>
            ) : null}
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1680px] overflow-x-hidden px-4 py-6 lg:px-6">
        <Outlet context={{ health, healthError, refreshHealth } satisfies AppShellContext} />
      </main>
    </div>
  )
}
