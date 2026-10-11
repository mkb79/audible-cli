import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, api } from '../api/client'
import { MARKETPLACES, WITH_USERNAME_MARKETPLACES } from '../constants'

export function ConnectAudiblePage() {
  const navigate = useNavigate()
  const [marketplace, setMarketplace] = useState('us')
  const [withUsername, setWithUsername] = useState(false)
  const [loginUrl, setLoginUrl] = useState<string | null>(null)
  const [pastedUrl, setPastedUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const generateLink = async () => {
    setBusy(true)
    setError(null)
    try {
      const res = await api.startAudibleConnect(marketplace, withUsername)
      setLoginUrl(res.login_url)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not generate a login link.')
    } finally {
      setBusy(false)
    }
  }

  const completeLogin = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.completeAudibleConnect(pastedUrl)
      navigate('/')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not complete the login.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page">
      <h1>Connect your Audible account</h1>

      {!loginUrl && (
        <div className="card">
          <label>
            Marketplace
            <select
              value={marketplace}
              onChange={(e) => {
                setMarketplace(e.target.value)
                if (!WITH_USERNAME_MARKETPLACES.has(e.target.value)) setWithUsername(false)
              }}
            >
              {MARKETPLACES.map((m) => (
                <option key={m.code} value={m.code}>
                  {m.label}
                </option>
              ))}
            </select>
          </label>

          <label className="checkbox">
            <input
              type="checkbox"
              checked={withUsername}
              disabled={!WITH_USERNAME_MARKETPLACES.has(marketplace)}
              onChange={(e) => setWithUsername(e.target.checked)}
            />
            Log in with an Audible-only ("pre-Amazon") account
          </label>

          <button onClick={generateLink} disabled={busy}>
            Generate Login Link
          </button>
        </div>
      )}

      {loginUrl && (
        <div className="card">
          <p>
            1. Open this link in a new tab and sign in with your Amazon credentials:
            <br />
            <a href={loginUrl} target="_blank" rel="noreferrer">
              {loginUrl}
            </a>
          </p>
          <p>
            2. After signing in — and solving a captcha, if asked, a second time — your browser will land on a
            "Page not found" error. That's expected. Copy the full URL from the address bar at that point.
          </p>
          <label>
            Paste the URL here
            <input
              type="text"
              value={pastedUrl}
              onChange={(e) => setPastedUrl(e.target.value)}
              placeholder="https://www.amazon.com/ap/maplanding?..."
            />
          </label>
          <div className="button-row">
            <button onClick={completeLogin} disabled={busy || !pastedUrl}>
              Finish Connecting
            </button>
            <button className="secondary" onClick={() => setLoginUrl(null)} disabled={busy}>
              Back
            </button>
          </div>
        </div>
      )}

      {error && <p className="error">{error}</p>}
    </div>
  )
}
