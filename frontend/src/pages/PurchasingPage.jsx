import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { useAuth } from '../lib/AuthContext.jsx'
import { getAllPages, requestJSON } from '../lib/api.js'
import { COMPLETED_PURCHASE_STATUSES } from '../lib/purchaseStatus.js'
import MockPurchasingPage from './MockPurchasingPage.jsx'
import OrderTracking, { trackingLabel } from './OrderTracking.jsx'

const tabs = [
  ['Waiting for Quotes', ['WAITING_FOR_QUOTES']],
  ['Pending Approval', ['PENDING_APPROVAL']],
  ['Order Placed', ['PLACED']],
  ['In Transit', ['IN_TRANSIT', 'PARTIALLY_RECEIVED']],
  ['Completed', COMPLETED_PURCHASE_STATUSES],
]
const money = value => value == null ? '—' : new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(Number(value))
const number = value => value == null ? '—' : Number(value).toLocaleString('en-IN')
const date = value => value ? new Date(value).toLocaleString() : '—'
const message = err => typeof err?.message === 'string' ? err.message : 'The request could not be completed.'

export default function PurchasingPage() {
  const { token, user } = useAuth()
  const [workflow, setWorkflow] = useState(null)
  const [workflowError, setWorkflowError] = useState('')
  useEffect(() => {
    if (!token || user?.role !== 'OWNER') return
    const controller = new AbortController()
    requestJSON('/api/purchase-requests/mock-workflow', { token, signal: controller.signal })
      .then(setWorkflow)
      .catch(err => { if (!controller.signal.aborted) setWorkflowError(message(err)) })
    return () => controller.abort()
  }, [token, user?.role])

  if (!token || user?.role !== 'OWNER') return <Navigate to="/" replace />
  if (workflowError) return <p className="error-banner" role="alert">{workflowError} <Link to="/">Return to Overview</Link></p>
  if (!workflow) return <p className="content">Loading Purchasing…</p>
  return workflow.enabled ? <MockPurchasingPage approvalLimit={workflow.approval_limit} /> : <DetailedPurchasingPage />
}

function DetailedPurchasingPage() {
  const { token, user, logout } = useAuth()
  const [orders, setOrders] = useState([])
  const [items, setItems] = useState([])
  const [tab, setTab] = useState(0)
  const [selected, setSelected] = useState(null)
  const [details, setDetails] = useState({})
  const [choice, setChoice] = useState('')
  const [comments, setComments] = useState('')
  const [refresh, setRefresh] = useState(0)
  const [busy, setBusy] = useState('')
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [detailError, setDetailError] = useState('')
  const [detailRefresh, setDetailRefresh] = useState(0)
  const canView = Boolean(token && user?.role === 'OWNER')
  useEffect(() => {
    if (!canView) return
    const controller = new AbortController()
    setLoading(true)
    Promise.all([getAllPages('/api/purchase-requests', token, controller.signal), getAllPages('/api/inventory', token, controller.signal)])
      .then(([p, i]) => { if (!controller.signal.aborted) { setOrders(p); setItems(i); setError('') } })
      .catch(err => { if (!controller.signal.aborted) { setError(message(err)); if (err.status === 401) logout() } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [canView, token, refresh, logout])
  const current = orders.find(o => o.id === selected)
  useEffect(() => {
    if (!canView || !current) { setDetails({}); return }
    const controller = new AbortController()
    const id = current.id
    setDetailLoading(true); setDetailError('')
    const get = path => requestJSON(`/api/purchase-requests/${id}/${path}`, { token, signal: controller.signal })
    const collection = get('quotations')
    const comparison = collection.then(response => {
      const canCompare = ['WAITING_FOR_QUOTES', 'PENDING_APPROVAL'].includes(current.status)
      return canCompare && response.collection_state === 'READY_FOR_COMPARISON'
        ? get('compare-quotations')
        : null
    }, () => null)
    Promise.allSettled([collection, comparison, get('ai-recommendation'), get('order-email-dispatch'), get('stock-receipts'), get('approval-history')])
      .then(results => {
        if (controller.signal.aborted) return
        const keys = ['collection', 'comparison', 'ai', 'dispatch', 'receipts', 'history']
        const data = Object.fromEntries(results.map((r, i) => [keys[i], r.status === 'fulfilled' ? r.value : null]))
        setDetails(data)
        setChoice(previous => data.comparison?.quotations.some(q =>
          q.eligible_for_comparison && String(q.quotation_id) === previous) ? previous : '')
        const failed = results.filter(r => r.status === 'rejected')
        if (failed.length) setDetailError(failed.map(r => message(r.reason)).join(' · '))
      })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false) })
    return () => controller.abort()
  }, [canView, token, selected, current?.status, detailRefresh])
  const inventoryById = useMemo(() => new Map(items.map(i => [i.id, i])), [items])
  const visible = orders.filter(o => tabs[tab][1].includes(o.tracking_status || o.status))
  const quoteRows = details.comparison?.quotations || []
  const eligible = quoteRows.filter(q => q.eligible_for_comparison)
  const selectedQuote = eligible.find(q => String(q.quotation_id) === choice)
  const aiPick = details.ai?.recommended_quotation_id
  const isPending = current?.status === 'PENDING_APPROVAL'
  const isWaiting = current?.status === 'WAITING_FOR_QUOTES'
  const isPlaced = Boolean(current?.tracking_status)
  const run = useCallback(async (label, path, body, confirmation) => {
    if (!current || busy) return
    if (confirmation && !window.confirm(confirmation)) return
    setBusy(label); setError(''); setNotice('')
    try {
      const result = await requestJSON(`/api/purchase-requests/${current.id}/${path}`, { token, method: 'POST', ...(body === undefined ? {} : { body }) })
      if (path === 'mock-delivery') {
        setNotice(result.replayed ? `Order #${current.id} was already delivered.`
          : `Order #${current.id} delivered. ${result.quantity_received} units restocked.`)
      } else {
        setNotice(`${label} completed. ${result?.dispatch_status ? `Dispatch: ${result.dispatch_status}.` : ''} ${result?.new_simulated_replies != null ? `${result.new_simulated_replies} simulated replies created.` : ''}`)
      }
      const nextTab = tabs.findIndex(([, statuses]) => statuses.includes(result?.order_status || result?.status))
      if (nextTab >= 0) setTab(nextTab)
      setRefresh(n => n + 1); setDetailRefresh(n => n + 1)
    } catch (err) {
      setError(message(err))
      if (err.status === 401) logout()
    } finally { setBusy('') }
  }, [current, busy, token, logout])
  if (!canView) return <Navigate to="/" replace />
  return <div className="dashboard-shell">
    <aside className="sidebar"><div className="brand"><span className="brand-icon">▥</span> ShelfSync</div><div className="nav-heading">WORKSPACE</div><Link className="nav-item" to="/">▦ Overview</Link><Link className="nav-item" to="/inventory">▤ Inventory</Link><Link className="nav-item" to="/vendors">▧ Vendors</Link><div className="nav-item selected">▣ Purchasing</div><Link className="nav-item" to="/checkout">▣ Checkout</Link><div className="sidebar-bottom"><Link to="/setup">Connectivity</Link></div></aside>
    <main className="dashboard-main"><header className="topbar"><span>Operations / <strong>Purchasing</strong></span><div className="topbar-actions"><span>{user?.name} · OWNER</span><button className="button-light" onClick={logout}>Sign out</button></div></header>
      <div className="content"><div className="heading-row"><div><h1>Purchase & Approval Center</h1></div><button className="button-light" disabled={loading || !!busy} onClick={() => setRefresh(x => x + 1)}>↻ Refresh</button></div>
      {error && <p role="alert" className="error-banner">{error}</p>}{notice && <p role="status" className="success-banner">{notice}</p>}
      <section className="panel purchase-panel"><div className="purchase-tabs" role="tablist" aria-label="Purchase order status">{tabs.map(([label, statuses], i) => <button key={label} role="tab" aria-selected={tab === i} className={tab === i ? 'purchase-tab active' : 'purchase-tab'} disabled={!!busy} onClick={() => { setTab(i); setSelected(null); setDetails({}); setChoice(''); setComments('') }}>{label} <strong>{orders.filter(o => statuses.includes(o.tracking_status || o.status)).length}</strong></button>)}</div>
        {loading ? <p className="subtle">Loading purchase requests…</p> : visible.length === 0 ? <p className="empty">{orders.length ? 'No orders in this category.' : <>No purchase requests yet. <Link to="/">Go to Overview</Link></>}</p> : <div className="purchase-list">{visible.map(o => <button key={o.id} className={selected === o.id ? 'purchase-order selected-order' : 'purchase-order'} disabled={!!busy} onClick={() => { if (o.id !== selected) { setDetails({}); setChoice(''); setComments('') } setSelected(o.id); setNotice(''); setError('') }}><span><strong>#{o.id} · {inventoryById.get(o.inventory_item_id)?.name || `Item ${o.inventory_item_id}`}</strong><small>{inventoryById.get(o.inventory_item_id)?.sku || 'SKU unavailable'} · {o.ordered_quantity} units · {date(o.created_at)}</small></span><span className="purchase-order-end"><span className="stock-pill healthy">{trackingLabel(o) || o.status.replaceAll('_', ' ')}</span><strong>{money(o.total_amount)}</strong></span></button>)}</div>}
      </section>
      {current && <section className="panel purchase-detail"><div className="heading-row"><div><h2>Order #{current.id} · {inventoryById.get(current.inventory_item_id)?.name || 'Inventory item'}</h2><p className="subtle">{inventoryById.get(current.inventory_item_id)?.sku || 'SKU unavailable'} · Required: {number(current.ordered_quantity)} · Received: {number(current.received_quantity)} · Status: {trackingLabel(current) || current.status.replaceAll('_', ' ')}</p></div><button className="button-light" onClick={() => setDetailRefresh(x => x + 1)} disabled={detailLoading}>↻ Reload details</button></div>
        {detailLoading && <p className="subtle">Loading quotation and order details…</p>}{detailError && <p className="error-banner" role="alert">{detailError}</p>}
        {details.collection && <div className="purchase-summary"><div><strong>{details.collection.quotations?.length || 0}</strong><small>Vendors contacted</small></div><div><strong>{details.collection.received_count}</strong><small>Quotes received</small></div><div><strong>{details.collection.requested_count}</strong><small>Still pending</small></div><div><strong>{details.collection.collection_state.replaceAll('_',' ')}</strong><small>Collection state</small></div></div>}
        {details.collection?.quote_deadline_at && <p className="subtle">Quotation deadline: {date(details.collection.quote_deadline_at)}</p>}
        {isWaiting && <div className="purchase-actions"><button className="button-light" disabled={!!busy} onClick={() => run('Prepare vendor quotations', 'request-quotations', undefined, 'Prepare quotation requests for eligible mapped vendors?')}>Prepare quotations</button><button className="button-light" disabled={!!busy} onClick={() => run('Mock/send RFQs', 'dispatch-quotation-requests', undefined, 'Dispatch vendor quotation requests? In SMTP mode this may send real email.')}>Dispatch RFQs</button><button className="button-light" disabled={!!busy} onClick={() => run('Simulate vendor replies', 'simulate-vendor-quote-replies', undefined, 'Generate fictional demo quotations for outstanding vendors? This is allowed only in simulation mode.')}>Simulate vendor replies</button><button className="button-light" disabled={!!busy} onClick={() => run('Close quotation window', 'close-quotations', undefined, 'Close the quotation window?')}>Close collection</button></div>}
        {details.collection?.quotations?.length > 0 && <div className="table-scroll"><table><thead><tr><th>Contacted vendor</th><th>Response</th><th>RFQ email</th><th>Quotation type</th></tr></thead><tbody>{details.collection.quotations.map(q => <tr key={q.id}><td>{q.vendor_name}</td><td>{q.status}</td><td>{q.dispatch_status}</td><td>{q.is_simulated ? <span className="stock-pill low">SIMULATED — DEMO ONLY</span> : q.status === 'RECEIVED' ? 'Actual' : 'Pending'}</td></tr>)}</tbody></table></div>}
        <h3>Vendor quotation comparison</h3>
        {quoteRows.length ? <div className="table-scroll"><table><thead><tr><th>Vendor</th><th>Last price</th><th>New quoted price</th><th>Delivery days</th><th>Total cost</th><th>Reliability</th><th>Eligibility</th></tr></thead><tbody>{quoteRows.map(q => <tr key={q.quotation_id}><td><strong>{q.vendor_name || `Vendor ${q.vendor_id}`}</strong>{aiPick === q.quotation_id && <small className="ai-pick">AI Pick · advisory</small>}{q.is_simulated && <small className="simulated-label">SIMULATED — NOT REAL</small>}</td><td>{money(q.last_known_unit_price)}</td><td>{money(q.quoted_unit_price)}</td><td>{number(q.quoted_delivery_days)}</td><td>{money(q.total_quoted_cost)}</td><td>{q.reliability?.completed_orders_with_delivery_data ? `${number(q.reliability.on_time_rate_percent)}% (${q.reliability.completed_orders_with_delivery_data} orders)` : 'No delivery history'}</td><td>{q.eligible_for_comparison ? <span className="stock-pill healthy">Eligible</span> : <span title={q.ineligibility_reasons?.join('; ')} className="stock-pill low">Ineligible</span>}</td></tr>)}</tbody></table></div> : <p className="empty">No quotations available for comparison yet.</p>}
        {details.ai?.reason && <div className="ai-reason"><strong>AI recommendation (advisory only)</strong><p>{details.ai.reason}</p></div>}
        {isWaiting && !detailLoading && !eligible.length && <p className="subtle">Close the quotation window with an eligible quote to request an AI recommendation.</p>}
        {isWaiting && <div className="purchase-actions"><button className="button-light" disabled={!!busy || detailLoading || !eligible.length} onClick={() => run('Generate AI recommendation', 'ai-recommendation', undefined, 'Request a Groq/Claude recommendation? This may use provider credits. It cannot authorize an order.')}>Get AI recommendation</button><button className="button-primary" disabled={!!busy || detailLoading || !eligible.length} onClick={() => run('Evaluate backend rules', 'evaluate', { quotation_id: selectedQuote?.quotation_id ?? null }, 'Evaluate the selected quotation using backend purchasing rules? This may automatically authorize an eligible order; no email is sent by evaluation.')}>Evaluate for auto-order / approval</button></div>}
        {(isWaiting || isPending) && eligible.length > 0 && <div className="purchase-decision"><label>Selected eligible quotation<select value={choice} onChange={e => setChoice(e.target.value)}><option value="">Backend default / no manual selection</option>{eligible.map(q => <option key={q.quotation_id} value={q.quotation_id}>{q.vendor_name} — {money(q.quoted_unit_price)} {q.is_simulated ? '(SIMULATED)' : ''}</option>)}</select></label></div>}
        {isPending && <div className="purchase-decision"><p className="subtle">Review the price and delivery time before approving.</p><label>Owner comments<textarea rows="2" maxLength="2000" value={comments} onChange={e => setComments(e.target.value)} placeholder="Reason for your decision"/></label><div className="purchase-actions"><button className="button-primary" disabled={!!busy || detailLoading || !selectedQuote} onClick={() => run('Owner approval', 'owner-decision', { action: 'APPROVED', quotation_id: Number(choice), comments: comments || null }, 'Approve this purchase using the selected fresh quotation? This authorizes the order, but does not send an email.')}>Approve selected quotation</button><button className="button-light" disabled={!!busy} onClick={() => run('Owner rejection', 'owner-decision', { action: 'REJECTED', comments: comments || null }, 'Reject this purchase request?')}>Reject request</button></div></div>}
        {isPlaced && <><h3>Order delivery and dispatch</h3><p>Final authorized amount: <strong>{money(current.total_amount)}</strong> · Receipt progress: <strong>{number(current.received_quantity)} / {number(current.ordered_quantity)}</strong></p><p className="subtle">Final email: {details.dispatch?.dispatch_status || details.dispatch?.status || 'NOT SENT / UNAVAILABLE'}</p>{details.receipts?.receipts?.length > 0 && <p className="subtle">{details.receipts.receipts.length} recorded receipt(s).</p>}{['PLACED', 'IN_TRANSIT', 'PARTIALLY_RECEIVED'].includes(current.status) && <button className="button-primary" disabled={!!busy} onClick={() => run('Dispatch final purchase order', 'dispatch-order-email', undefined, 'Dispatch this authorized purchase order? In SMTP mode this may send a REAL email. Simulated selected quotes are blocked from real SMTP by the backend.')}>Dispatch authorized order email</button>}</>}
        {details.history?.approvals?.length > 0 && <><h3>Owner decision history</h3><ul>{details.history.approvals.map(a => <li key={a.id}>{a.action} · {date(a.created_at)} {a.comments ? `— ${a.comments}` : ''}</li>)}</ul></>}
        {isPlaced && <div className="purchase-actions">
          <OrderTracking order={current} disabled={!!busy}
            onTransit={() => run('Mark as In Transit', 'mock-transit')}
            onDelivery={() => run('Mark as Delivered', 'mock-delivery', undefined, 'Mark as delivered and restock the remaining units?')} />
        </div>}
      </section>}
      </div>
    </main>
  </div>
}
