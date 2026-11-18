import { useEffect, useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { useAuth } from '../lib/AuthContext.jsx'
import { requestJSON } from '../lib/api.js'

const price = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' })

export default function VendorsPage() {
  const { token, user, logout } = useAuth()
  const [catalogue, setCatalogue] = useState(null)
  const [refresh, setRefresh] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const authorized = Boolean(token && user?.role === 'OWNER')

  useEffect(() => {
    if (!authorized) return
    const controller = new AbortController()
    setLoading(true)
    setError('')
    requestJSON('/api/vendors/sample-quotations', { token, method: 'POST', signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) setCatalogue(result) })
      .catch(err => {
        if (controller.signal.aborted) return
        if (err.status === 401) logout()
        else setError(err.message)
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [authorized, token, refresh, logout])

  if (!authorized) return <Navigate to="/" replace />

  const products = new Map()
  for (const quote of catalogue?.quotations ?? []) {
    if (!products.has(quote.inventory_item_id)) products.set(quote.inventory_item_id, { ...quote, quotes: new Map() })
    products.get(quote.inventory_item_id).quotes.set(quote.vendor_id, quote)
  }

  return <div className="dashboard-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-icon">▥</span> ShelfSync</div>
      <div className="nav-heading">WORKSPACE</div>
      <Link className="nav-item" to="/">▦ Overview</Link>
      <Link className="nav-item" to="/inventory">▤ Inventory</Link>
      <div className="nav-item selected" aria-current="page">▧ Vendors</div>
      <Link className="nav-item" to="/purchasing">▣ Purchasing</Link>
      <Link className="nav-item" to="/checkout">▣ Checkout</Link>
      <div className="sidebar-bottom"><Link to="/setup">Connectivity</Link></div>
    </aside>
    <main className="dashboard-main">
      <header className="topbar"><span>Operations / <strong>Vendors</strong></span>
        <div className="topbar-actions"><span>{user.name} · OWNER</span><button className="button-light" onClick={logout}>Sign out</button></div>
      </header>
      <div className="content">
        <div className="heading-row">
          <div><h1>Vendors</h1><p className="subtle">Sample prices and delivery times.</p></div>
          <button className="button-light" disabled={loading} onClick={() => setRefresh(count => count + 1)}>↻ Refresh</button>
        </div>
        {loading && <p className="subtle" role="status">Loading sample quotations…</p>}
        {error && <p className="error-banner" role="alert">{error}</p>}
        {catalogue && <>
          <div className="panel-grid">
            {catalogue.vendors.map(vendor => <section className="panel" key={vendor.id}>
              <h2>{vendor.name}</h2>
              <p>{vendor.email}</p>
              <p className="subtle">{vendor.phone}</p>
            </section>)}
          </div>
          <section className="panel">
            <div className="panel-heading"><div><h2>Mock quotations</h2></div></div>
            {products.size === 0 ? <p className="empty">No inventory products to quote.</p> : <div className="table-scroll">
              <table>
                <thead><tr><th scope="col">Product</th>{catalogue.vendors.map(vendor => <th scope="col" key={vendor.id}>{vendor.name}</th>)}</tr></thead>
                <tbody>{[...products.values()].map(product => <tr key={product.inventory_item_id}>
                  <th scope="row"><strong>{product.product_name}</strong><br/><small>{product.sku} · {product.unit}</small></th>
                  {catalogue.vendors.map(vendor => {
                    const quote = product.quotes.get(vendor.id)
                    return <td key={vendor.id}>{quote ? <><strong>{price.format(quote.unit_price)}</strong><br/>{Number(quote.delivery_days)} days</> : 'Quote unavailable'}</td>
                  })}
                </tr>)}</tbody>
              </table>
            </div>}
          </section>
        </>}
      </div>
    </main>
  </div>
}
