import { accountLabel } from '../lib/accountLabel.js'
import Icon from '../components/Icon.jsx'
import OwnerOverview from '../components/OwnerOverview.jsx'
import { useCallback, useEffect, useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { useAuth } from '../lib/AuthContext.jsx'
import { ApiError, getAllPages, requestJSON } from '../lib/api'
import { useStockStatusRefresh } from '../lib/useStockStatusRefresh.js'
import Sidebar from '../components/Sidebar.jsx'

export default function OwnerDashboard() {
  const [credentials, setCredentials] = useState({ email: '', password: '' })
  const { token, user, login: saveSession, logout: clearSession } = useAuth()
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const logout = useCallback(() => {
    clearSession(); setData(null); setError('')
  }, [clearSession])
  const updateStock = useCallback((inventory, orders) => {
    setData(current => current && { ...current, inventory, orders })
  }, [])
  useStockStatusRefresh(token, user?.role === 'OWNER' && !!data && !loading, updateStock, logout)
  const login = async (event) => {
    event.preventDefault(); setLoading(true); setError('')
    try {
      const result = await requestJSON('/api/auth/login', { method: 'POST', body: credentials })
      const owner = await requestJSON('/api/auth/me', { token: result.access_token })
      if (!['OWNER', 'EMPLOYEE'].includes(owner.role)) { setError('This account cannot access ShelfSync.'); return }
      saveSession(result.access_token, owner); setCredentials({ email: '', password: '' })
    } catch (e) { setError(e.message) } finally { setLoading(false) }
  }
  useEffect(() => {
    if (!token || user?.role !== 'OWNER') return
    const controller = new AbortController()
    setLoading(true); setError('')
    Promise.all([
      getAllPages('/api/inventory', token, controller.signal),
      getAllPages('/api/purchase-requests', token, controller.signal),
      requestJSON('/api/inventory/value-trend', { token, signal: controller.signal }).catch(error => {
        if (error instanceof ApiError && error.status === 401) throw error
        return null
      }),
    ]).then(([inventory, orders, values]) => {
      if (!controller.signal.aborted) setData({ inventory, orders, values })
    }).catch(e => {
      if (controller.signal.aborted) return
      if (e instanceof ApiError && e.status === 401) { logout(); setError('Session expired. Please sign in again.') }
      else setError(e.message)
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [token, user?.role, refresh, logout])
  if (token && user?.role === 'EMPLOYEE') return <Navigate to="/checkout" replace />
  return <div className="dashboard-shell">
    {token && <Sidebar />}
    <main className="dashboard-main">
      {token && (
        <header className="topbar">
          <span>Operations / <strong>Overview</strong></span>
          {user && (
            <div className="topbar-actions">
              <span>{accountLabel(user)}</span>
              <button onClick={logout} className="button-light">Sign out</button>
            </div>
          )}
        </header>
      )}
      {!token ? <div className="login-stage">
        <section className="login-intro"><h1>Welcome to <em>ShelfSync</em></h1></section>
        <section className="login-panel"><div className="login-brand">ShelfSync</div><h2>Sign in</h2><form onSubmit={login}><label>Email<input type="email" required autoComplete="username" value={credentials.email} onChange={e=>setCredentials(c=>({...c,email:e.target.value}))}/></label><label>Password<input type="password" required autoComplete="current-password" value={credentials.password} onChange={e=>setCredentials(c=>({...c,password:e.target.value}))}/></label><button disabled={loading} className="button-primary">{loading?'Signing in…':'Sign in to ShelfSync'}</button></form>
          {error && <p className="error" role="alert">{error}</p>}
          <Link to="/contact" className="login-contact"><Icon name="contact" />Contact</Link>
        </section>
      </div> : (
        <OwnerOverview data={data} loading={loading} error={error} onRefresh={() => setRefresh(value => value + 1)} />
      )}
    </main>
  </div>
}
