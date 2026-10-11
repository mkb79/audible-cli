import { useEffect, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { AbsLibrary, AbsSettingsInfo, TenantUser } from '../api/types'

export function AdminSettingsPage() {
  const [absSettings, setAbsSettings] = useState<AbsSettingsInfo | null>(null)
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [savingAbs, setSavingAbs] = useState(false)
  const [absError, setAbsError] = useState<string | null>(null)

  const [libraries, setLibraries] = useState<AbsLibrary[]>([])
  const [users, setUsers] = useState<TenantUser[]>([])
  const [mappingDrafts, setMappingDrafts] = useState<Record<number, { libraryId: string; folderId: string }>>({})
  const [savingUserId, setSavingUserId] = useState<number | null>(null)

  const loadAll = () => {
    api.adminGetAbsSettings().then((s) => {
      setAbsSettings(s)
      setBaseUrl(s.base_url)
    })
    api
      .adminListAbsLibraries()
      .then(setLibraries)
      .catch(() => setLibraries([]))
    api.adminListUsers().then((list) => {
      setUsers(list)
      setMappingDrafts(
        Object.fromEntries(
          list.map((u) => [u.id, { libraryId: u.abs_library_id ?? '', folderId: u.abs_folder_id ?? '' }]),
        ),
      )
    })
  }

  useEffect(loadAll, [])

  const saveAbsSettings = async () => {
    setSavingAbs(true)
    setAbsError(null)
    try {
      const saved = await api.adminSaveAbsSettings(baseUrl, apiKey)
      setAbsSettings(saved)
      setApiKey('')
      loadAll()
    } catch (err) {
      setAbsError(err instanceof ApiError ? err.message : 'Could not save Audiobookshelf settings.')
    } finally {
      setSavingAbs(false)
    }
  }

  const saveMapping = async (userId: number) => {
    const draft = mappingDrafts[userId]
    if (!draft?.libraryId || !draft?.folderId) return
    setSavingUserId(userId)
    try {
      await api.adminSetTenantMapping(userId, draft.libraryId, draft.folderId)
      loadAll()
    } finally {
      setSavingUserId(null)
    }
  }

  const foldersFor = (libraryId: string) => libraries.find((l) => l.id === libraryId)?.folders ?? []

  return (
    <div className="page">
      <h1>Admin settings</h1>

      <div className="card">
        <h2>Audiobookshelf server</h2>
        <label>
          Server URL
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://audiobookshelf.example.com"
          />
        </label>
        <label>
          API key {absSettings?.api_key_set && <span className="muted">(already set — leave blank to keep it)</span>}
          <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
        </label>
        <button onClick={saveAbsSettings} disabled={savingAbs}>
          Test &amp; Save
        </button>
        {absError && <p className="error">{absError}</p>}
      </div>

      <div className="card">
        <h2>Tenant → library mapping</h2>
        <table className="data-table">
          <thead>
            <tr>
              <th>User</th>
              <th>Audible</th>
              <th>Library</th>
              <th>Folder</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => {
              const draft = mappingDrafts[u.id] ?? { libraryId: '', folderId: '' }
              return (
                <tr key={u.id}>
                  <td>
                    {u.display_name ?? u.email ?? `User #${u.id}`}
                    {u.is_admin && <span className="muted"> (admin)</span>}
                  </td>
                  <td>{u.audible_connected ? 'connected' : '—'}</td>
                  <td>
                    <select
                      value={draft.libraryId}
                      onChange={(e) =>
                        setMappingDrafts((prev) => ({
                          ...prev,
                          [u.id]: { libraryId: e.target.value, folderId: '' },
                        }))
                      }
                    >
                      <option value="">—</option>
                      {libraries.map((lib) => (
                        <option key={lib.id} value={lib.id}>
                          {lib.name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <select
                      value={draft.folderId}
                      disabled={!draft.libraryId}
                      onChange={(e) =>
                        setMappingDrafts((prev) => ({
                          ...prev,
                          [u.id]: { ...prev[u.id], folderId: e.target.value },
                        }))
                      }
                    >
                      <option value="">—</option>
                      {foldersFor(draft.libraryId).map((f) => (
                        <option key={f.id} value={f.id}>
                          {f.name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <button
                      onClick={() => saveMapping(u.id)}
                      disabled={savingUserId === u.id || !draft.libraryId || !draft.folderId}
                    >
                      Save
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
