import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../lib/AuthContext.jsx'
import { getAllPages, requestJSON } from '../lib/api.js'
import OrderTracking, { trackingLabel } from './OrderTracking.jsx'

const labels = {
  WAITING_FOR_QUOTES: 'Waiting for AI',
  PENDING_APPROVAL: 'Needs Owner approval',
  PLACED: 'Placed',
  IN_TRANSIT: 'In transit',
  PARTIALLY_RECEIVED: 'Partially received',
  RECEIVED: 'Delivered',
  REJECTED: 'Rejected',
}
const currency = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 })

function QuoteComparison({ order, token }) {
  const [expanded, setExpanded] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!expanded) return
    const controller = new AbortController()
    setResult(null)
    setError('')
    requestJSON(`/api/purchase-requests/${order.id}/mock-comparison`, { token, signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) setResult(data) })
      .catch(err => { if (!controller.signal.aborted) setError(err.message) })
    return () => controller.abort()
  }, [expanded, order.id, order.status, token])

  return <details onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary>Compare vendor quotations</summary>
    {expanded && !result && !error && <p className="subtle" role="status">Loading quotations…</p>}
    {error && <p className="error-banner" role="alert">{error}</p>}
    {result && <div className="table-scroll">
      <table>
        <thead><tr><th scope="col">Vendor</th><th scope="col">Unit price</th><th scope="col">Availability</th><th scope="col">Delivery estimate</th><th scope="col">Total</th></tr></thead>
        <tbody>{result.quotations.map(quote => <tr key={quote.quotation_id}>
          <th scope="row">{quote.vendor_name}{quote.vendor_name === order.vendor_name && ' · Selected'}</th>
          <td>{quote.quoted_unit_price == null ? '—' : currency.format(quote.quoted_unit_price)}</td>
          <td>{quote.available_quantity ? `${quote.available_quantity} / ${quote.required_quantity} units` : 'Unavailable for this quantity'}</td>
          <td>{quote.quoted_delivery_days == null ? '—' : `${Number(quote.quoted_delivery_days)} days`}</td>
          <td>{quote.total_quoted_cost == null ? '—' : currency.format(quote.total_quoted_cost)}</td>
        </tr>)}</tbody>
      </table>
    </div>}
  </details>
}

export default function MockPurchasingPage({ approvalLimit }) {
  const { token, user, logout } = useAuth()
  const [data, setData] = useState(null)
  const [reload, setReload] = useState(0)
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const hasWaitingOrders = data?.orders.some(order => order.status === 'WAITING_FOR_QUOTES')

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      getAllPages('/api/purchase-requests', token, controller.signal),
      getAllPages('/api/inventory', token, controller.signal),
    ]).then(([orders, items]) => {
      if (!controller.signal.aborted) setData({ orders, items: new Map(items.map(item => [item.id, item])) })
    }).catch(err => {
      if (!controller.signal.aborted) setError(err.message)
    })
    return () => controller.abort()
  }, [token, reload])

  useEffect(() => {
    if (!hasWaitingOrders || busy != null) return
    const timer = window.setInterval(() => setReload(count => count + 1), 5000)
    return () => window.clearInterval(timer)
  }, [hasWaitingOrders, busy])

  const act = async (order, path, body, confirmation) => {
    if (confirmation && !window.confirm(confirmation)) return
    setBusy(order.id)
    setError('')
    setNotice('')
    try {
      const result = await requestJSON(`/api/purchase-requests/${order.id}/${path}`, { token, method: 'POST', body })
      const state = result.order_status || result.status
      const isTracking = path === 'mock-transit' || path === 'mock-delivery'
      setNotice(path === 'mock-delivery'
        ? (result.replayed ? `Order #${order.id} was already delivered.`
          : `Order #${order.id} delivered. ${result.quantity_received} units restocked.`)
        : `Order #${order.id} · ${trackingLabel({ tracking_status: state }) || labels[state] || state}`)
      if (isTracking || order.status !== state) {
        setData(current => ({
          ...current,
          orders: current.orders.map(row => row.id === order.id
            ? { ...row, ...(isTracking ? { tracking_status: state } : { status: state }) }
            : row),
        }))
      }
      setReload(count => count + 1)
    } catch (err) {
      if (err.status === 401) logout()
      else setError(err.message)
    } finally {
      setBusy(null)
    }
  }

  const disabled = busy != null
  return <div className="dashboard-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-icon">▥</span> ShelfSync</div>
      <div className="nav-heading">WORKSPACE</div>
      <Link className="nav-item" to="/">▦ Overview</Link>
      <Link className="nav-item" to="/inventory">▤ Inventory</Link>
      <Link className="nav-item" to="/vendors">▧ Vendors</Link>
      <div className="nav-item selected">▣ Purchasing</div>
      <Link className="nav-item" to="/checkout">▣ Checkout</Link>
    </aside>
    <main className="dashboard-main">
      <header className="topbar">
        <span>Operations / <strong>Purchasing</strong></span>
        <div className="topbar-actions"><span>{user.name} · OWNER</span><button className="button-light" onClick={logout}>Sign out</button></div>
      </header>
      <div className="content">
        <div className="heading-row">
          <div>
            <h1>Purchasing</h1>
            <p className="subtle">Simulated vendor emails. AI selects the vendor. Orders over {currency.format(approvalLimit)} need Owner approval.</p>
          </div>
          <button className="button-light" disabled={disabled} onClick={() => { setError(''); setReload(count => count + 1) }}>↻ Refresh</button>
        </div>
        {error && <p className="error-banner" role="alert">{error}</p>}
        {notice && <p className="success-banner" role="status">{notice}</p>}
        {!data && !error && <p>Loading orders…</p>}
        {data?.orders.length === 0 && <p className="panel empty">Purchase requests appear when you request a first order or a checkout triggers replenishment.</p>}
        {data?.orders.map(order => <section className="panel purchase-detail" key={order.id}>
          <div className="heading-row">
            <div>
              <h2>#{order.id} · {data.items.get(order.inventory_item_id)?.name || `Item ${order.inventory_item_id}`}</h2>
              <p className="subtle">{order.ordered_quantity} units · {order.unit_price == null ? 'Unit price pending' : currency.format(order.unit_price) + ' per unit'} · {order.total_amount == null ? 'Total pending' : currency.format(order.total_amount) + ' total'}</p>
            </div>
            <span className="pill">{trackingLabel(order) || labels[order.status] || order.status}</span>
          </div>
          {order.ai_recommendation_reason && <p className="ai-reason">{order.ai_recommendation_reason}</p>}
          {order.mock_order && <QuoteComparison order={order} token={token} />}
          {order.mock_order && ['PLACED', 'IN_TRANSIT', 'RECEIVED'].includes(order.status) && <p className="subtle">Mock purchase email recorded</p>}
          <div className="purchase-actions">
            {order.status === 'WAITING_FOR_QUOTES' && <button className="button-light" disabled={disabled} onClick={() => act(order, 'mock-process')}>Retry AI ordering</button>}
            {order.mock_order && order.status === 'PENDING_APPROVAL' && <>
              <button className="button-primary" disabled={disabled} onClick={() => act(order, 'owner-decision', { action: 'APPROVED' }, 'Approve this order and record the mock email?')}>Approve order</button>
              <button className="button-light" disabled={disabled} onClick={() => act(order, 'owner-decision', { action: 'REJECTED' }, 'Reject this purchase order?')}>Reject order</button>
            </>}
            <OrderTracking order={order} disabled={disabled}
              onTransit={() => act(order, 'mock-transit')}
              onDelivery={() => act(order, 'mock-delivery', undefined, 'Mark as delivered and restock the remaining units?')} />
          </div>
        </section>)}
      </div>
    </main>
  </div>
}
