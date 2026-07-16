import { Activity, LayoutDashboard, Menu, Plus, Scissors, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
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
  const [refreshKey, setRefreshKey] = useState(0)

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

  return (
    <div className="min-h-screen bg-app-bg text-app-text">
      <header className="sticky top-0 z-20 border-b border-app-border bg-app-bg/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1500px] items-center gap-4 px-4 py-3 lg:px-6">
          <NavLink to="/" className="flex min-w-0 items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-md border border-app-accent/50 bg-app-accent/15 text-app-accent">
              <Scissors className="h-5 w-5" aria-hidden="true" />
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-semibold text-app-text">Podcast Shorts Cutter</span>
              <span className="block text-xs text-app-muted">Product UI v0.5</span>
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

          <div className="hidden items-center gap-2 lg:flex">
            <div className="rounded-md border border-app-border bg-app-panelAlt px-3 py-2 text-xs text-app-muted">
              <span className="mr-2 inline-flex items-center gap-1 text-app-text">
                <Activity className="h-3.5 w-3.5 text-app-accent" aria-hidden="true" />
                Backend
              </span>
              {healthError ? 'Unavailable' : health?.status ?? 'Checking'}
            </div>
            <StatusBadge value={healthError ? 'failed' : 'ready'} tone={healthError ? 'danger' : 'success'} />
            <div className="rounded-md border border-app-border bg-app-panelAlt px-3 py-2 text-xs text-app-muted">
              {reviewerReadyLabel(health)}
            </div>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1500px] px-4 py-6 lg:px-6">
        <Outlet context={{ health, healthError, refreshHealth } satisfies AppShellContext} />
      </main>
    </div>
  )
}
