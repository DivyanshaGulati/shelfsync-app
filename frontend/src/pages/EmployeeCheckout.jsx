import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { useAuth } from '../lib/AuthContext.jsx'
import { ApiError, getAllPages, requestJSON } from '../lib/api.js'

// The key belongs to a single immutable checkout attempt, not a button click.
// Keep it on transport failures so retries cannot deduct stock twice.
function newKey() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return `checkout-${crypto.randomUUID()}`
  throw new Error('Secure checkout requires a browser with crypto.randomUUID support.')
}
function describeError(error) {
  if (error instanceof ApiError) {
    if (error.status === 409) return `${error.message}. Refresh stock and review your request before changing it.`
    if (error.status === 422) return `Invalid checkout request: ${error.message}`
    if (error.status === 403) return 'Your account is not permitted to check out stock.'
  }
  return error?.message || 'Checkout could not be completed. Retry with the same request.'
}
export default function EmployeeCheckout() {
  const { token, user, logout } = useAuth()
  const [items, setItems] = useState([])
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState('')
  const [quantity, setQuantity] = useState('1')
  const [note, setNote] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(null)
  const [refresh, setRefresh] = useState(0)
  const attempt = useRef(null)
  const inFlight = useRef(false)
  const isOwner = user?.role === 'OWNER'
  const checkoutTitle = isOwner ? 'Owner Checkout' : 'Employee Checkout'
  const allowed = Boolean(token && ['OWNER', 'EMPLOYEE'].includes(user?.role))
  useEffect(() => {
    if (!allowed) return
    const controller = new AbortController()
    setLoading(true)
    getAllPages('/api/inventory', token, controller.signal)
      .then(rows => { if (!controller.signal.aborted) setItems(rows) })
      .catch(err => { if (!controller.signal.aborted) { setError(describeError(err)); if (err instanceof ApiError && err.status === 401) logout() } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [allowed, token, refresh, logout])
  const matches = useMemo(() => items.filter(item => `${item.name} ${item.sku} ${item.category}`.toLowerCase().includes(query.trim().toLowerCase())), [items, query])
  const item = items.find(row => String(row.id) === selected)
  const available = item ? Math.max(0, Number(item.on_hand) - Number(item.reserved_qty)) : 0
  const count = Number(quantity)
  const valid = item && Number.isSafeInteger(count) && count > 0 && count <= available && note.length <= 5000
  if (!allowed) return <Navigate to="/" replace />
  function changeRequest(fn) { if (saving) return; attempt.current = null; setSuccess(null); setError(''); fn() }
  async function reload() { setRefresh(n => n + 1) }
  async function submit(event) {
    event.preventDefault()
    if (!valid || inFlight.current) return
    const body = { inventory_item_id: item.id, quantity: count, usage_note: note.trim() || null }
    const signature = JSON.stringify(body)
    try {
      if (!attempt.current || attempt.current.signature !== signature) attempt.current = { signature, key: newKey() }
    } catch (err) { setError(describeError(err)); return }
    inFlight.current = true; setSaving(true); setError(''); setSuccess(null)
    try {
      const response = await requestJSON('/api/checkout', { token, method: 'POST', body: { ...body, idempotency_key: attempt.current.key } })
      setSuccess({ ...response, itemName: item.name, quantity: count })
      attempt.current = null
      setItems(rows => rows.map(row => row.id === item.id ? { ...row, on_hand: response.on_hand } : row))
      setQuantity('1'); setNote(''); setSelected('')
      // A confirmed response (including a replay) ends this attempt.
      reload()
    } catch (err) {
      setError(describeError(err))
      if (err instanceof ApiError && err.status === 401) logout()
      // Do not discard the key: the server may have committed before a network failure.
    } finally { inFlight.current = false; setSaving(false) }
  }
  return <div className="dashboard-shell">
    <aside className="sidebar"><div className="brand"><span className="brand-icon">▥</span> ShelfSync</div><div className="nav-heading">WORKSPACE</div>{isOwner && <><Link className="nav-item" to="/">▦ Overview</Link><Link className="nav-item" to="/inventory">▤ Inventory</Link><Link className="nav-item" to="/vendors">▧ Vendors</Link><Link className="nav-item" to="/purchasing">▣ Purchasing</Link></>}<div className="nav-item selected">▣ {isOwner ? 'Checkout' : checkoutTitle}</div><div className="sidebar-bottom"><span className="status-dot"/> PostgreSQL-backed</div></aside>
    <main className="dashboard-main"><header className="topbar"><span>Operations / <strong>Checkout</strong></span><div className="topbar-actions"><span>{user?.name || user?.email} · {user?.role}</span><button className="button-light" onClick={logout}>Sign out</button></div></header>
      <div className="content"><div className="heading-row"><div><div className="eyebrow">LIVE STOCK</div><h1>{checkoutTitle}</h1><p className="subtle">Record actual items used. Available stock excludes reserved quantities.</p></div><button className="button-light" type="button" disabled={loading || saving} onClick={reload}>↻ Refresh stock</button></div>
        {error && <p role="alert" className="error-banner">{error}</p>}
        {success && <section role="status" className="success-banner"><strong>{success.replayed ? 'Checkout already recorded' : 'Checkout completed'}</strong><p>{success.quantity} × {success.itemName} · Transaction #{success.transaction.id}. Remaining available: {success.available_stock}.</p></section>}
        <section className="panel checkout-panel"><h2>Record item usage</h2><p className="subtle">Every confirmed checkout is applied atomically by FastAPI and recorded in PostgreSQL.</p>
          <form className="checkout-form" onSubmit={submit}>
            <label>Search inventory<input type="search" value={query} onChange={e => changeRequest(() => { setQuery(e.target.value); setSelected('') })} placeholder="Item name, SKU, or category" aria-label="Search inventory" disabled={saving}/></label>
            <label>Select item<select value={selected} required onChange={e => changeRequest(() => setSelected(e.target.value))} disabled={loading || saving}><option value="">Choose an inventory item</option>{matches.map(row => <option key={row.id} value={row.id}>{row.name} ({row.sku}) — {Math.max(0, row.on_hand - row.reserved_qty)} available</option>)}</select></label>
            {loading && <p className="subtle">Loading inventory…</p>}
            {!loading && matches.length === 0 && <p className="subtle">No matching items found.</p>}
            {item && <div className="checkout-stock"><div><small>On hand</small><strong>{item.on_hand}</strong></div><div><small>Reserved</small><strong>{item.reserved_qty}</strong></div><div><small>Available</small><strong>{available} {item.unit}</strong></div></div>}
            <label>Quantity<input type="number" required min="1" max={available || undefined} step="1" inputMode="numeric" value={quantity} onChange={e => changeRequest(() => setQuantity(e.target.value))} disabled={saving}/></label>
            {item && available === 0 && <p className="error">No unreserved stock is available for this item.</p>}
            {item && Number.isFinite(count) && count > available && <p className="error">Requested quantity exceeds available stock ({available}).</p>}
            <label>Usage note (optional)<textarea value={note} maxLength="5000" rows="3" placeholder="Where or why was the item used?" onChange={e => changeRequest(() => setNote(e.target.value))} disabled={saving}/></label>
            <p className="subtle">Confirm only once. If the network fails, retry the unchanged request; ShelfSync reuses its idempotency key.</p>
            <button className="button-primary" type="submit" disabled={!valid || saving || loading}>{saving ? 'Recording checkout…' : `Confirm checkout${valid ? ` · ${count} ${item.unit}` : ''}`}</button>
          </form></section>
      </div></main></div>
}
