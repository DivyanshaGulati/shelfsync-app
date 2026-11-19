import { accountLabel } from '../lib/accountLabel.js'
import ProductIdentity from '../components/ProductIdentity.jsx'
import Icon from '../components/Icon.jsx'
import PageTitle from '../components/PageTitle.jsx'
import ProductImage from '../components/ProductImage.jsx'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../lib/AuthContext.jsx'
import { ApiError, getAllPages, requestJSON } from '../lib/api.js'
import { addCheckoutItem, availableCheckoutStock } from '../lib/checkoutCart.js'
import Sidebar from '../components/Sidebar.jsx'

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
  const cartLine = cart.find(line => line.item.id === item?.id)
  const available = item ? availableCheckoutStock(item) : 0
  const addable = Math.max(0, available - (cartLine?.quantity ?? 0))
  const count = Number(quantity)
  const valid = item && !cartLine?.attempted && Number.isSafeInteger(count) && count > 0 && count <= addable && note.length <= 5000
  const stockById = new Map(items.map(row => [row.id, availableCheckoutStock(row)]))
  const invalidLine = line => !line.attempted && line.quantity > (stockById.get(line.item.id) ?? 0)
  const stockChanged = !loading && cart.some(invalidLine)
  const hasUnconfirmed = cart.some(line => line.attempted)
  if (!allowed) return <Navigate to="/" replace />
  function changeRequest(fn) { if (saving) return; setSuccess(null); setError(''); fn() }
  async function reload() { setRefresh(n => n + 1) }
  function addItem(event) {
    event.preventDefault()
    if (!valid || loading || saving || inFlight.current) return
    try {
      setCart(addCheckoutItem(cart, item, count, note))
      setSelected('')
      setQuantity('1')
      setNote('')
      setError('')
      setSuccess(null)
    } catch (err) { setError(describeError(err)) }
  }

  async function completeCheckout() {
    if (!cart.length || loading || stockChanged || inFlight.current) return
    inFlight.current = true
    setSaving(true)
    setError('')
    setSuccess(null)
    let completed = 0
    try {
      for (const line of cart) {
        const body = { inventory_item_id: line.item.id, quantity: line.quantity, usage_note: line.note, idempotency_key: line.key }
        // A lost response can leave a committed checkout. Keep its exact payload for retry.
        setCart(lines => lines.map(entry => entry.key === line.key ? { ...entry, attempted: true } : entry))
        try {
          await requestJSON('/api/checkout', { token, method: 'POST', body })
        } catch (err) {
          const stockRejected = err instanceof ApiError && err.status === 409 && err.message === 'Insufficient available stock'
          const newAttemptRejected = !line.attempted && err instanceof ApiError && [403, 404, 409, 422].includes(err.status)
          if (stockRejected || newAttemptRejected) {
            setCart(lines => lines.map(entry => entry.key === line.key ? { ...entry, attempted: false } : entry))
          }
          throw err
        }
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
            <label>Select item<select value={selected} required onChange={e => changeRequest(() => setSelected(e.target.value))} disabled={loading || saving}><option value="">Choose an inventory item</option>{matches.map(row => <option key={row.id} value={row.id}>{row.name} ({row.sku}) — {availableCheckoutStock(row)} available</option>)}</select></label>
            <div className="quantity-field">
              <label htmlFor="checkout-quantity">Quantity</label>
              <div className="quantity-stepper">
                <button type="button" aria-label="Decrease quantity" disabled={saving || count <= 1}
                  onClick={() => changeRequest(() => setQuantity(String(Math.max(1, count - 1))))}>−</button>
                <input id="checkout-quantity" type="number" required min="1" max={addable || undefined}
                  step="1" inputMode="numeric" value={quantity} onChange={e => changeRequest(() => setQuantity(e.target.value))} disabled={saving} />
                <button type="button" aria-label="Increase quantity" disabled={saving || loading || !item || cartLine?.attempted || count >= addable}
                  onClick={() => changeRequest(() => setQuantity(String(count + 1)))}>+</button>
              </div>
            </div>
            <button className="button-primary" type="submit" disabled={!valid || saving || loading}>+ Add to Checkout</button>
            {loading && <p className="subtle">Loading inventory…</p>}
            {!loading && matches.length === 0 && <p className="subtle">No matching items found.</p>}
            {item && <div className="checkout-stock"><ProductImage sku={item.sku} name={item.name} /><div><small>On hand</small><strong>{item.on_hand}</strong></div><div><small>Reserved</small><strong>{item.reserved_qty}</strong></div><div><small>Available to add</small><strong>{addable} {item.unit}</strong></div></div>}
            {item && (cartLine?.attempted
              ? <p className="subtle">Retry checkout before adding more of this item.</p>
              : addable === 0
                ? <p className="error">{cartLine ? 'No more units can be added to this checkout.' : 'No unreserved stock is available for this item.'}</p>
                : count > addable && <p className="error">You can add up to {addable} more.</p>)}
            <details className="checkout-options">
              <summary>Search or add a note</summary>
            <label>Search inventory<input type="search" value={query} onChange={e => changeRequest(() => { setQuery(e.target.value); setSelected('') })} placeholder="Item name, SKU, or category" aria-label="Search inventory" disabled={saving}/></label>
              <label>Usage note (optional)<textarea value={note} maxLength="5000" rows="3" placeholder="Where or why was the item used?" onChange={e => changeRequest(() => setNote(e.target.value))} disabled={saving}/></label>
            </details>
          </form></section>
        <section className="panel cart-panel">
          <div className="heading-row"><h2 className="section-title"><Icon name="layers" /> Items in this Checkout ({cart.length})</h2>
            <button className="button-light" disabled={!cart.length || saving || hasUnconfirmed} onClick={() => setCart([])}>Clear all</button>
          </div>
          {stockChanged && <p className="error-banner" role="alert">Available stock changed. Remove the affected item and add it again with a lower quantity.</p>}
          {hasUnconfirmed && !saving && <p className="subtle">Retry checkout to confirm the pending item.</p>}
          {cart.length ? <div className="table-scroll"><table><thead><tr><th>Item / SKU</th><th>Quantity</th><th>Action</th></tr></thead>
            <tbody>{cart.map(line => <tr key={line.key}><td><ProductIdentity name={line.item.name} sku={line.item.sku} unit={line.item.unit} category={line.item.category} thumbnail={line.item.thumbnail} /></td>
              <td>{line.quantity}{!loading && invalidLine(line) && <small>{stockById.get(line.item.id) ?? 0} available</small>}</td><td><button className="button-light" disabled={saving || line.attempted} aria-label={`Remove ${line.item.name}`} onClick={() => setCart(lines => lines.filter(entry => entry.key !== line.key))}><Icon name="trash" /></button></td>
            </tr>)}</tbody></table></div> : <p className="subtle">Add an item to start your checkout.</p>}
        </section>
        <button className="button-primary checkout-complete" disabled={!cart.length || saving || loading || stockChanged} onClick={completeCheckout}>{saving ? 'Recording…' : hasUnconfirmed ? 'Retry Checkout' : 'Complete Checkout'}</button>
      </div></main></div>
}
