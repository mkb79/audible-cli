import type { ImportRunItem } from '../api/types'
import { StatusBadge } from './StatusBadge'

export function RunStatusTable({ items }: { items: ImportRunItem[] }) {
  if (items.length === 0) {
    return <p className="muted">No books in this run yet.</p>
  }

  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Title</th>
          <th>ASIN</th>
          <th>Status</th>
          <th>Error</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.id}>
            <td>{item.title}</td>
            <td className="mono">{item.asin}</td>
            <td>
              <StatusBadge status={item.status} />
            </td>
            <td className="muted">{item.error_message ?? ''}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
