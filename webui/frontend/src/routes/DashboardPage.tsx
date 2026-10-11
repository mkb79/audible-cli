import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ApiError, api } from '../api/client'
import type { AudibleAccountInfo, ImportReadiness, ImportRunDetail } from '../api/types'
import { RunStatusTable } from '../components/RunStatusTable'
import { StatusBadge } from '../components/StatusBadge'
import { usePolling } from '../hooks/usePolling'

export function DashboardPage() {
  const navigate = useNavigate()
  const [account, setAccount] = useState<AudibleAccountInfo | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [triggerError, setTriggerError] = useState<string | null>(null)
  const [triggering, setTriggering] = useState(false)
  const [readiness, setReadiness] = useState<ImportReadiness | null>(null)

  useEffect(() => {
    api
      .audibleAccount()
      .then(setAccount)
      .catch((err) => setLoadError(String(err)))
    // Best-effort: if this fails, the import request still reports the reason.
    api
      .importReadiness()
      .then(setReadiness)
      .catch(() => setReadiness(null))
  }, [])

  const { data: run } = usePolling<ImportRunDetail | null>(() => api.currentImport())
  const isRunning = run != null && run.status === 'running'

  const trigger = async () => {
    setTriggering(true)
    setTriggerError(null)
    try {
      await api.triggerImport()
      window.location.reload()
    } catch (err) {
      setTriggerError(err instanceof ApiError ? err.message : 'Could not start the import.')
    } finally {
      setTriggering(false)
    }
  }

  if (loadError) return <p className="error">{loadError}</p>
  if (!account) return <p className="muted">Loading…</p>

  if (!account.connected) {
    return (
      <div className="page">
        <h1>Dashboard</h1>
        <p>Your Audible account isn't connected yet.</p>
        <button onClick={() => navigate('/connect-audible')}>Connect Audible Account</button>
      </div>
    )
  }

  return (
    <div className="page">
      <h1>Dashboard</h1>
      <p className="muted">
        Audible account connected ({account.marketplace}) — <StatusBadge status={account.status ?? 'active'} />
        {account.status === 'needs_reconnect' && (
          <>
            {' '}
            <Link to="/connect-audible">Reconnect</Link>
          </>
        )}
      </p>

      {isRunning ? (
        <div className="card">
          <h2>Import in progress</h2>
          <RunStatusTable items={run.items} />
        </div>
      ) : (
        <div className="card">
          {readiness && !readiness.ready && <p className="error">{readiness.reason}</p>}
          <button
            onClick={trigger}
            disabled={triggering || account.status === 'needs_reconnect' || readiness?.ready === false}
          >
            {triggering ? 'Starting…' : 'Trigger Import'}
          </button>
          {triggerError && <p className="error">{triggerError}</p>}
        </div>
      )}

      <p>
        <Link to="/runs">View import history</Link>
      </p>
    </div>
  )
}
