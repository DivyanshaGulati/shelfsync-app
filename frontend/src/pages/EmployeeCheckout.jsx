import { accountLabel } from '../lib/accountLabel.js'
import ProductIdentity from '../components/ProductIdentity.jsx'
import Icon from '../components/Icon.jsx'
import PageTitle from '../components/PageTitle.jsx'
import ProductImage from '../components/ProductImage.jsx'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../lib/AuthContext.jsx'
import { ApiError, getAllPages, requestJSON } from '../lib/api.js'
import Sidebar from '../components/Sidebar.jsx'

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
  const [cart, setCart] = useState([])
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
  function changeRequest(fn) { if (saving) return; setSuccess(null); setError(''); fn() }
  async function reload() { setRefresh(n => n + 1) }
  function addItem(event) {
    event.preventDefault()
    if (!valid || saving) return
    const queued = cart.filter(line => line.item.id === item.id).reduce((sum, line) => sum + line.quantity, 0)
    if (queued + count > available) {
      setError('The combined quantity exceeds available stock.')
      return
    }
    try {
      const line = { item, quantity: count, note: note.trim() || null, key: newKey() }
      setCart(lines => [...lines, line])
      setSelected('')
      setQuantity('1')
      setNote('')
      setError('')
      setSuccess(null)
    } catch (err) { setError(describeError(err)) }
  }

  async function completeCheckout() {
    if (!cart.length || inFlight.current) return
    inFlight.current = true
    setSaving(true)
    setError('')
    setSuccess(null)
    let completed = 0
    try {
      for (const line of cart) {
        const body = { inventory_item_id: line.item.id, quantity: line.quantity, usage_note: line.note, idempotency_key: line.key }
        await requestJSON('/api/checkout', { token, method: 'POST', body })
        completed += 1
        setCart(lines => lines.filter(entry => entry.key !== line.key))
      }
    } catch (err) {
      setError(describeError(err))
      if (err instanceof ApiError && err.status === 401) logout()
    } finally {
      setSuccess(completed || null)
      reload()
      inFlight.current = false
      setSaving(false)
    }
  }
  return <div className="dashboard-shell">
    <Sidebar employee={!isOwner} />
    <main className="dashboard-main"><header className="topbar"><span>Operations / <strong>Checkout</strong></span><div className="topbar-actions"><span>{accountLabel(user)}</span><button className="button-light" onClick={logout}>Sign out</button></div></header>
      <div className="content"><div className="heading-row"><PageTitle icon="/purchasing" title={checkoutTitle} subtitle="Record items taken out and update stock." /><button className="button-light" type="button" disabled={loading || saving} onClick={reload}>↻ Refresh stock</button></div>
        {error && <p role="alert" className="error-banner">{error}</p>}
        {success && <p className="success-banner" role="status">{success} checkout {success === 1 ? 'entry' : 'entries'} recorded.</p>}
        <section className="panel checkout-panel">
          <form className="checkout-form" onSubmit={addItem}>
            <label>Select item<select value={selected} required onChange={e => changeRequest(() => setSelected(e.target.value))} disabled={loading || saving}><option value="">Choose an inventory item</option>{matches.map(row => <option key={row.id} value={row.id}>{row.name} ({row.sku}) — {Math.max(0, row.on_hand - row.reserved_qty)} available</option>)}</select></label>
            <div className="quantity-field">
              <label htmlFor="checkout-quantity">Quantity</label>
              <div className="quantity-stepper">
                <button type="button" aria-label="Decrease quantity" disabled={saving || count <= 1}
                  onClick={() => changeRequest(() => setQuantity(String(Math.max(1, count - 1))))}>−</button>
                <input id="checkout-quantity" type="number" required min="1" max={available || undefined}
                  step="1" inputMode="numeric" value={quantity} onChange={e => changeRequest(() => setQuantity(e.target.value))} disabled={saving} />
                <button type="button" aria-label="Increase quantity" disabled={saving || !item || count >= available}
                  onClick={() => changeRequest(() => setQuantity(String(count + 1)))}>+</button>
              </div>
            </div>
            <button className="button-primary" type="submit" disabled={!valid || saving || loading}>+ Add to Checkout</button>
            {loading && <p className="subtle">Loading inventory…</p>}
            {!loading && matches.length === 0 && <p className="subtle">No matching items found.</p>}
            {item && <div className="checkout-stock"><ProductImage sku={item.sku} name={item.name} /><div><small>On hand</small><strong>{item.on_hand}</strong></div><div><small>Reserved</small><strong>{item.reserved_qty}</strong></div><div><small>Available</small><strong>{available} {item.unit}</strong></div></div>}
            {item && available === 0 && <p className="error">No unreserved stock is available for this item.</p>}
            {item && Number.isFinite(count) && count > available && <p className="error">Requested quantity exceeds available stock ({available}).</p>}
            <details className="checkout-options">
              <summary>Search or add a note</summary>
            <label>Search inventory<input type="search" value={query} onChange={e => changeRequest(() => { setQuery(e.target.value); setSelected('') })} placeholder="Item name, SKU, or category" aria-label="Search inventory" disabled={saving}/></label>
              <label>Usage note (optional)<textarea value={note} maxLength="5000" rows="3" placeholder="Where or why was the item used?" onChange={e => changeRequest(() => setNote(e.target.value))} disabled={saving}/></label>
            </details>
          </form></section>
        <section className="panel cart-panel">
          <div className="heading-row"><h2 className="section-title"><Icon name="layers" /> Items in this Checkout ({cart.length})</h2>
            <button className="button-light" disabled={!cart.length || saving} onClick={() => setCart([])}>Clear all</button>
          </div>
          {cart.length ? <div className="table-scroll"><table><thead><tr><th>Item / SKU</th><th>Quantity</th><th>Action</th></tr></thead>
            <tbody>{cart.map(line => <tr key={line.key}><td><ProductIdentity name={line.item.name} sku={line.item.sku} unit={line.item.unit} /></td>
              <td>{line.quantity}</td><td><button className="button-light" disabled={saving} aria-label={`Remove ${line.item.name}`} onClick={() => setCart(lines => lines.filter(entry => entry.key !== line.key))}><Icon name="trash" /></button></td>
            </tr>)}</tbody></table></div> : <p className="subtle">Add an item to start your checkout.</p>}
        </section>
        <button className="button-primary checkout-complete" disabled={!cart.length || saving || loading} onClick={completeCheckout}>{saving ? 'Recording…' : 'Complete Checkout'}</button>
      </div></main></div>
}
