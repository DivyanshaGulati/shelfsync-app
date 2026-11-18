import Icon from '../components/Icon.jsx'
import PageTitle from '../components/PageTitle.jsx'
import ProductIdentity from '../components/ProductIdentity.jsx'
import { useEffect, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../lib/AuthContext.jsx'
import { requestJSON } from '../lib/api.js'
import Sidebar from '../components/Sidebar.jsx'

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
    <Sidebar />
    <main className="dashboard-main">
      <header className="topbar"><span>Operations / <strong>Vendors</strong></span>
        <div className="topbar-actions"><span>{user.name} · OWNER</span><button className="button-light" onClick={logout}>Sign out</button></div>
      </header>
      <div className="content">
        <div className="heading-row">
          <PageTitle icon="/vendors" title="Vendors" subtitle="Compare supplier prices and delivery times." />
          <button className="button-light" disabled={loading} onClick={() => setRefresh(count => count + 1)}>↻ Refresh</button>
        </div>
        {loading && <p className="subtle" role="status">Loading sample quotations…</p>}
        {error && <p className="error-banner" role="alert">{error}</p>}
        {catalogue && <>
          <div className="vendor-grid">
            {catalogue.vendors.map(vendor => <section className="panel vendor-card" key={vendor.id}><span className="icon-tile"><Icon name="/vendors" /></span><div>
              <h2>{vendor.name}</h2>
              <p>{vendor.email}</p>
              <p className="subtle">{vendor.phone}</p></div>
            </section>)}
          </div>
          <section className="panel">
            <div className="panel-heading"><div><h2 className="section-title"><Icon name="quote" /> Mock Quotations</h2></div></div>
            {products.size === 0 ? <p className="empty">No inventory products to quote.</p> : <div className="table-scroll">
              <table>
                <thead><tr><th scope="col">Product</th>{catalogue.vendors.map(vendor => <th scope="col" key={vendor.id}>{vendor.name}</th>)}</tr></thead>
                <tbody>{[...products.values()].map(product => <tr key={product.inventory_item_id}>
                  <th scope="row"><ProductIdentity name={product.product_name} sku={product.sku} unit={product.unit} /></th>
                  {catalogue.vendors.map(vendor => {
                    const quote = product.quotes.get(vendor.id)
                    return <td key={vendor.id}>{quote ? <><strong>{price.format(quote.unit_price)}</strong> <span className="delivery-days">{Number(quote.delivery_days)} days</span></> : 'Quote unavailable'}</td>
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
