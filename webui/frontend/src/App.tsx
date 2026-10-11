import { useEffect, useState } from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import { api } from './api/client'
import type { Me } from './api/types'
import { AdminSettingsPage } from './routes/AdminSettingsPage'
import { ConnectAudiblePage } from './routes/ConnectAudiblePage'
import { DashboardPage } from './routes/DashboardPage'
import { RunDetailPage } from './routes/RunDetailPage'
import { RunHistoryPage } from './routes/RunHistoryPage'

export function App() {
  const [me, setMe] = useState<Me | null>(null)

  useEffect(() => {
    api.me().then(setMe)
  }, [])

  const handleLogout = async () => {
    await api.logout()
    window.location.href = '/'
  }

  return (
    <div className="app">
      <nav className="nav">
        <NavLink to="/">Dashboard</NavLink>
        <NavLink to="/runs">History</NavLink>
        {me?.is_admin && <NavLink to="/admin">Admin</NavLink>}
        <span className="nav-spacer" />
        {me && <span className="muted">{me.display_name ?? me.email}</span>}
        <button className="link-button" onClick={handleLogout}>
          Log out
        </button>
      </nav>

      <main>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/connect-audible" element={<ConnectAudiblePage />} />
          <Route path="/runs" element={<RunHistoryPage />} />
          <Route path="/runs/:runId" element={<RunDetailPage />} />
          {me?.is_admin && <Route path="/admin" element={<AdminSettingsPage />} />}
        </Routes>
      </main>
    </div>
  )
}
