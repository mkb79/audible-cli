import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { ImportRunSummary } from '../api/types'
import { StatusBadge } from '../components/StatusBadge'

export function RunHistoryPage() {
  const [runs, setRuns] = useState<ImportRunSummary[] | null>(null)

  useEffect(() => {
    api.listImports().then(setRuns)
  }, [])

  if (!runs) return <p className="muted">Loading…</p>

  return (
    <div className="page">
      <h1>Import history</h1>
      {runs.length === 0 ? (
        <p className="muted">No imports yet.</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Requested</th>
              <th>Status</th>
              <th>Books</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td>{new Date(run.requested_at).toLocaleString()}</td>
                <td>
                  <StatusBadge status={run.status} />
                </td>
                <td>{run.total_items ?? '—'}</td>
                <td>
                  <Link to={`/runs/${run.id}`}>Details</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
