import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { useAuth } from '../lib/AuthContext.jsx'
import { ApiError, getAllPages, requestJSON } from '../lib/api'
import { COMPLETED_PURCHASE_STATUSES, inventoryRows } from '../lib/purchaseStatus.js'
import { useStockStatusRefresh } from '../lib/useStockStatusRefresh.js'

const money = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 })
const dayKey = (value) => new Date(value).toISOString().slice(0, 10)
const todayUTC = () => new Date().toISOString().slice(0, 10)

export function dashboardMetrics(inventory, orders, transactions, today = todayUTC()) {
  const rows = inventoryRows(inventory, orders)
  const days = Array.from({ length: 14 }, (_, i) => {
    const d = new Date(`${today}T00:00:00Z`)
    d.setUTCDate(d.getUTCDate() - (13 - i))
    const key = d.toISOString().slice(0, 10)
    return { key, date: d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', timeZone: 'UTC' }), consumed: 0, received: 0 }
  })
  const lookup = new Map(days.map((d) => [d.key, d]))
  for (const tx of transactions) {
    const point = lookup.get(dayKey(tx.created_at))
    if (point && tx.transaction_type === 'CHECKOUT') point.consumed += Number(tx.quantity)
  }
  // Receipts are not included in /api/transactions: do not invent a receipt series.
  return {
    rows,
    inventoryValue: rows.reduce((sum, item) => sum + Number(item.on_hand) * Number(item.unit_cost), 0),
    lowStock: rows.filter(i => i.low).sort((a, b) => (a.availableStock - Number(a.reorder_point)) - (b.availableStock - Number(b.reorder_point))),
    pending: orders.filter(o => o.status === 'PENDING_APPROVAL'),
    inTransit: orders.filter(o => o.status === 'IN_TRANSIT' || o.status === 'PARTIALLY_RECEIVED' || (o.status === 'PLACED' && !o.mock_order)),
    waiting: orders.filter(o => o.status === 'WAITING_FOR_QUOTES'),
    completed: orders.filter(o => COMPLETED_PURCHASE_STATUSES.includes(o.status)),
    chart: days,
  }
}

export default function OwnerDashboard() {
  const [credentials, setCredentials] = useState({ email: '', password: '' })
  const { token, user, login: saveSession, logout: clearSession } = useAuth()
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [insight, setInsight] = useState(null)
  const [insightLoading, setInsightLoading] = useState(false)
  const [insightError, setInsightError] = useState('')
  const logout = useCallback(() => {
    clearSession(); setData(null); setError(''); setInsight(null); setInsightError('')
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
      getAllPages('/api/transactions', token, controller.signal),
    ]).then(([inventory, orders, transactions]) => {
      if (!controller.signal.aborted) { setData({ inventory, orders, transactions }); setLastUpdated(new Date()) }
    }).catch(e => {
      if (controller.signal.aborted) return
      if (e instanceof ApiError && e.status === 401) { logout(); setError('Session expired. Please sign in again.') }
      else setError(e.message)
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [token, user?.role, refresh, logout])
  const generateInsight = async () => {
    setInsightLoading(true)
    setInsightError('')
    setInsight(null)
    try {
      const result = await requestJSON('/api/inventory/ai-insights', { token, method: 'POST' })
      setInsight(result)
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) logout()
      else setInsightError(err.message)
    } finally {
      setInsightLoading(false)
    }
  }
  const metrics = useMemo(() => data ? dashboardMetrics(data.inventory, data.orders, data.transactions) : null, [data])
  if (token && user?.role === 'EMPLOYEE') return <Navigate to="/checkout" replace />
  return <div className="dashboard-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-icon">▥</span> ShelfSync</div>
      <div className="nav-heading">WORKSPACE</div>
      <div className="nav-item selected">▦ <span>Overview</span></div>
      <div className="nav-item muted"><Link to="/inventory">▤ <span>Inventory</span></Link></div>
      <Link className="nav-item" to="/vendors">▧ <span>Vendors</span></Link>
      <Link className="nav-item" to="/purchasing">▣ <span>Purchasing</span></Link>
      <Link className="nav-item" to="/checkout">▣ Checkout</Link><div className="sidebar-bottom"><Link to="/contact">Contact</Link></div>
    </aside>
    <main className="dashboard-main">
      <header className="topbar"><span>Operations / <strong>Overview</strong></span><div className="topbar-actions">{user && <><span>{user.name} · OWNER</span><button onClick={logout} className="button-light">Sign out</button></>}</div></header>
      {!token ? <section className="login-panel"><h1>Welcome to ShelfSync</h1><form onSubmit={login}><label>Email<input type="email" required autoComplete="username" value={credentials.email} onChange={e=>setCredentials(c=>({...c,email:e.target.value}))}/></label><label>Password<input type="password" required autoComplete="current-password" value={credentials.password} onChange={e=>setCredentials(c=>({...c,password:e.target.value}))}/></label><button disabled={loading} className="button-primary">{loading?'Signing in…':'Sign in to ShelfSync'}</button></form>{error && <p className="error" role="alert">{error}</p>}</section> : <div className="content">
        <div className="heading-row"><div><h1>Owner Dashboard</h1></div><button className="button-primary" disabled={loading} onClick={()=>setRefresh(x=>x+1)}>{loading?'Loading…':'↻ Refresh data'}</button></div>
        {error && <div className="error-banner" role="alert">{error}</div>}
        {lastUpdated && <p className="updated">Last refreshed: {lastUpdated.toLocaleString('en-IN')}</p>}
        {metrics ? <>
          <div className="metric-grid"><Metric label="Inventory value" value={money.format(metrics.inventoryValue)} foot="On-hand × unit cost"/><Metric label="Low-stock items" value={metrics.lowStock.length} foot="At or below reorder point" warning/><Metric label="Pending approvals" value={metrics.pending.length}/><Metric label="Orders in transit" value={metrics.inTransit.length}/></div>
          <div className="panel-grid"><section className="panel"><div className="panel-heading"><div><h2>Consumption trend</h2><p>Checkout quantities · last 14 days</p></div></div><div className="chart-wrap"><ResponsiveContainer width="100%" height="100%"><LineChart data={metrics.chart} margin={{ top: 12, right: 18, left: -18, bottom: 0 }}><CartesianGrid strokeDasharray="3 4" stroke="#e9eef5"/><XAxis dataKey="date" tick={{fontSize:11}}/><YAxis allowDecimals={false} tick={{fontSize:11}}/><Tooltip/><Legend/><Line name="Units checked out" type="monotone" dataKey="consumed" stroke="#2563eb" strokeWidth={3} dot={false}/></LineChart></ResponsiveContainer></div></section>
          <section className="panel"><div className="panel-heading"><div><h2>Purchase pipeline</h2></div></div><div className="pipeline"><Pipeline label="Waiting for quotes" value={metrics.waiting.length}/><Pipeline label="Pending approval" value={metrics.pending.length}/><Pipeline label="In transit" value={metrics.inTransit.length}/><Pipeline label="Completed" value={metrics.completed.length}/></div></section></div>
          <div className="panel-grid"><section className="panel"><div className="panel-heading"><div><h2>Needs attention</h2></div><span className="pill">{metrics.lowStock.length} items</span></div><div className="table-scroll"><table><thead><tr><th>Item</th><th>On hand</th><th>On order</th><th>Reorder point</th></tr></thead><tbody>{metrics.lowStock.slice(0,8).map(item=><tr key={item.id}><td><strong>{item.name}</strong><small>{item.sku}</small></td><td>{item.on_hand}</td><td>{item.onOrder}</td><td>{Number(item.reorder_point).toFixed(1)}</td></tr>)}{metrics.lowStock.length===0&&<tr><td colSpan="4" className="empty">No low-stock items</td></tr>}</tbody></table></div></section>
          <section className="panel">
            <div className="panel-heading"><div><h2>AI inventory insights</h2></div></div>
            <button className="button-light" disabled={insightLoading} onClick={generateInsight}>
              {insightLoading ? 'Generating inventory insight…' : 'Generate inventory insight'}
            </button>
            {insightError && <p className="error" role="alert">{insightError}</p>}
            {insight && <div className="ai-reason" role="status">
              <p>{insight.summary}</p>
              {insight.priority_sku && <p>Priority SKU <strong>{insight.priority_sku}</strong></p>}
            </div>}
          </section></div>
        </> : <div className="panel loading-panel">{loading ? 'Loading dashboard…' : 'No data available yet.'}</div>}
      </div>}
    </main>
  </div>
}
function Metric({label,value,foot,warning}) { return <section className="metric"><div className="metric-label">{label}</div><div className={warning?'metric-value warning':'metric-value'}>{value}</div>{foot && <div className="metric-foot">{foot}</div>}</section> }
function Pipeline({label,value}) {return <div className="pipeline-row"><span>{label}</span><strong>{value}</strong></div>}
