import { useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { ImportRunDetail } from '../api/types'
import { RunStatusTable } from '../components/RunStatusTable'
import { StatusBadge } from '../components/StatusBadge'
import { usePolling } from '../hooks/usePolling'

function RunDetailInner({ runId }: { runId: number }) {
  const { data: run, error } = usePolling<ImportRunDetail>(() => api.getImport(runId))

  if (error) return <p className="error">{String(error)}</p>
  if (!run) return <p className="muted">Loading…</p>

  return (
    <div className="page">
      <h1>Import run #{run.id}</h1>
      <p className="muted">
        <StatusBadge status={run.status} /> — requested {new Date(run.requested_at).toLocaleString()}
      </p>
      {run.error_message && <p className="error">{run.error_message}</p>}
      <RunStatusTable items={run.items} />
    </div>
  )
}

// Keyed by runId so navigating between two run-detail pages (which React
// Router does not remount by default on param changes) resets the poll
// loop's state cleanly for the new run.
export function RunDetailPage() {
  const { runId } = useParams()
  return <RunDetailInner key={runId} runId={Number(runId)} />
}
