import { useEffect, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { AbsLibrary, AbsSettingsInfo, TenantUser } from '../api/types'

type TargetDraft = { libraryId: string; folderId: string }

const errorText = (err: unknown, fallback: string) => (err instanceof ApiError ? err.message : fallback)

export function AdminSettingsPage() {
  const [absSettings, setAbsSettings] = useState<AbsSettingsInfo | null>(null)
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [savingAbs, setSavingAbs] = useState(false)
  const [absError, setAbsError] = useState<string | null>(null)

  const [libraries, setLibraries] = useState<AbsLibrary[]>([])
  const [defaultDraft, setDefaultDraft] = useState<TargetDraft>({ libraryId: '', folderId: '' })
  const [savingDefault, setSavingDefault] = useState(false)
  const [defaultError, setDefaultError] = useState<string | null>(null)

  const [users, setUsers] = useState<TenantUser[]>([])
  // An empty libraryId means "use the default library".
  const [mappingDrafts, setMappingDrafts] = useState<Record<number, TargetDraft>>({})
  const [savingUserId, setSavingUserId] = useState<number | null>(null)
  const [mappingErrors, setMappingErrors] = useState<Record<number, string>>({})

  const loadAll = () => {
    api.adminGetAbsSettings().then((s) => {
      setAbsSettings(s)
      setBaseUrl(s.base_url)
      setDefaultDraft({ libraryId: s.default_library_id ?? '', folderId: s.default_folder_id ?? '' })
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

  // Audiobooks can only be uploaded into book libraries, never podcast ones.
  const bookLibraries = libraries.filter((l) => l.media_type === 'book')
  const foldersFor = (libraryId: string) => libraries.find((l) => l.id === libraryId)?.folders ?? []
  // Picking a library with a single folder picks that folder too.
  const draftForLibrary = (libraryId: string): TargetDraft => {
    const folders = foldersFor(libraryId)
    return { libraryId, folderId: folders.length === 1 ? folders[0].id : '' }
  }
  const describeTarget = (libraryId: string | null, folderId: string | null) => {
    const library = libraries.find((l) => l.id === libraryId)
    const folder = library?.folders.find((f) => f.id === folderId)
    return library && folder ? `${library.name} — ${folder.name}` : null
  }
  const defaultLabel = describeTarget(absSettings?.default_library_id ?? null, absSettings?.default_folder_id ?? null)

  const saveAbsSettings = async () => {
    setSavingAbs(true)
    setAbsError(null)
    try {
      const saved = await api.adminSaveAbsSettings(baseUrl, apiKey)
      setAbsSettings(saved)
      setApiKey('')
      loadAll()
    } catch (err) {
      setAbsError(errorText(err, 'Could not save Audiobookshelf settings.'))
    } finally {
      setSavingAbs(false)
    }
  }

  const saveDefault = async () => {
    setSavingDefault(true)
    setDefaultError(null)
    try {
      setAbsSettings(await api.adminSetDefaultTarget(defaultDraft.libraryId, defaultDraft.folderId))
    } catch (err) {
      setDefaultError(errorText(err, 'Could not save the default library.'))
    } finally {
      setSavingDefault(false)
    }
  }

  const saveMapping = async (userId: number) => {
    const draft = mappingDrafts[userId] ?? { libraryId: '', folderId: '' }
    if (draft.libraryId && !draft.folderId) return
    setSavingUserId(userId)
    setMappingErrors((prev) => {
      const next = { ...prev }
      delete next[userId]
      return next
    })
    try {
      if (draft.libraryId) {
        await api.adminSetTenantMapping(userId, draft.libraryId, draft.folderId)
      } else {
        await api.adminClearTenantMapping(userId)
      }
      loadAll()
    } catch (err) {
      setMappingErrors((prev) => ({ ...prev, [userId]: errorText(err, 'Could not save this mapping.') }))
    } finally {
      setSavingUserId(null)
    }
  }

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
        <h2>Default library</h2>
        <p className="muted">
          Imports go here for anyone without their own library below.{' '}
          {defaultLabel ? <>Currently: {defaultLabel}.</> : <>Not set — users without a library can't import.</>}
        </p>
        <label>
          Library
          <select
            value={defaultDraft.libraryId}
            disabled={!absSettings?.api_key_set}
            onChange={(e) => setDefaultDraft(draftForLibrary(e.target.value))}
          >
            <option value="">—</option>
            {bookLibraries.map((lib) => (
              <option key={lib.id} value={lib.id}>
                {lib.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Folder
          <select
            value={defaultDraft.folderId}
            disabled={!defaultDraft.libraryId}
            onChange={(e) => setDefaultDraft((prev) => ({ ...prev, folderId: e.target.value }))}
          >
            <option value="">—</option>
            {foldersFor(defaultDraft.libraryId).map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
        </label>
        <button onClick={saveDefault} disabled={savingDefault || !defaultDraft.libraryId || !defaultDraft.folderId}>
          Save default
        </button>
        {defaultError && <p className="error">{defaultError}</p>}
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
                        setMappingDrafts((prev) => ({ ...prev, [u.id]: draftForLibrary(e.target.value) }))
                      }
                    >
                      <option value="">{defaultLabel ? `Default (${defaultLabel})` : 'Default (not set)'}</option>
                      {bookLibraries.map((lib) => (
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
                      disabled={savingUserId === u.id || (!!draft.libraryId && !draft.folderId)}
                    >
                      Save
                    </button>
                    {mappingErrors[u.id] && <p className="error">{mappingErrors[u.id]}</p>}
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
