import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { useAuth } from '../lib/AuthContext.jsx'
import { ApiError, getAllPages, requestJSON } from '../lib/api.js'
import { inventoryRows } from '../lib/purchaseStatus.js'
import { useStockStatusRefresh } from '../lib/useStockStatusRefresh.js'
import Sidebar from '../components/Sidebar.jsx'

const categories = ['Fasteners', 'Plumbing', 'Electrical', 'Tools & Adhesives', 'Hardware Fittings']
const emptyForm = { name: '', sku: '', category: 'Fasteners', description: '', unit: 'pcs', unit_cost: '0.00' }
const rupees = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' })

export default function InventoryPage() {
  const { token, user, logout } = useAuth()
  const [items, setItems] = useState([])
  const [orders, setOrders] = useState([])
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState('All categories')
  const [refresh, setRefresh] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [editing, setEditing] = useState(null)
  const [form, setForm] = useState(emptyForm)
  const [initialOrderQuantity, setInitialOrderQuantity] = useState(null)
  const [saving, setSaving] = useState(false)
  const updateStock = useCallback((inventory, purchases) => {
    setItems(inventory)
    setOrders(purchases)
  }, [])
  useEffect(() => {
    if (!token || user?.role !== 'OWNER') return
    const controller = new AbortController()
    setLoading(true); setError('')
    Promise.all([getAllPages('/api/inventory', token, controller.signal), getAllPages('/api/purchase-requests', token, controller.signal)])
      .then(([inventory, purchases]) => { if (!controller.signal.aborted) updateStock(inventory, purchases) })
      .catch(e => { if (controller.signal.aborted) return; if (e instanceof ApiError && e.status === 401) logout(); setError(e.message) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [token, user?.role, refresh, logout, updateStock])
  useStockStatusRefresh(token, user?.role === 'OWNER' && !loading, updateStock, logout)
  const rows = useMemo(() => inventoryRows(items, orders), [items, orders])
  const filtered = useMemo(() => rows.filter(item =>
    (category === 'All categories' || item.category.toLowerCase() === category.toLowerCase()) &&
    (`${item.name} ${item.sku}`.toLowerCase().includes(search.trim().toLowerCase()))
  ), [rows, category, search])
  if (!token || user?.role !== 'OWNER') return <Navigate to="/" replace />
  function openForm(item) {
    setError(''); setNotice(''); setEditing(item ? item.id : 'new')
    const canRequestFirstOrder = !item ||
      (item.on_hand === 0 && !orders.some(order => order.inventory_item_id === item.id))
    setInitialOrderQuantity(canRequestFirstOrder ? '' : null)
    setForm(item ? { name: item.name, sku: item.sku, category: item.category, description: item.description || '', unit: item.unit, unit_cost: String(item.unit_cost) } : { ...emptyForm })
  }
  async function save(e) {
    e.preventDefault(); setSaving(true); setError('')
    try {
      const body = { ...form, name: form.name.trim(), sku: form.sku.trim(), category: form.category.trim(), unit: form.unit.trim(), unit_cost: form.unit_cost, description: form.description.trim() || null }
      const requestFirstOrder = initialOrderQuantity !== null && initialOrderQuantity !== ''
      if (requestFirstOrder) body.initial_order_quantity = Number(initialOrderQuantity)
      await requestJSON(editing === 'new' ? '/api/inventory' : `/api/inventory/${editing}`, { token, method: editing === 'new' ? 'POST' : 'PATCH', body })
      setEditing(null)
      if (requestFirstOrder) {
        setNotice(<>First order requested. <Link to="/purchasing">Track it in Purchasing</Link>.</>)
      } else {
        setNotice(editing === 'new' ? 'Item created. Stock starts at zero; use a verified receipt to add stock.' : 'Item details saved. Stock quantities were not changed.')
      }
      setRefresh(x => x + 1)
    } catch (err) { setError(err.message) } finally { setSaving(false) }
  }
  return <div className="dashboard-shell">
    <Sidebar />
    <main className="dashboard-main"><header className="topbar"><span>Operations / <strong>Inventory</strong></span><div className="topbar-actions"><span>{user?.name} · OWNER</span><button className="button-light" onClick={logout}>Sign out</button></div></header>
    <div className="content"><div className="heading-row"><div><h1>Inventory Management</h1></div><div className="inventory-actions"><button className="button-light" disabled={loading} onClick={() => setRefresh(x => x + 1)}>↻ Refresh</button><button className="button-primary" onClick={() => openForm(null)}>+ Add Item</button></div></div>
    {notice && <p className="success-banner" role="status">{notice}</p>}{error && <p className="error-banner" role="alert">{error}</p>}
    <section className="panel inventory-panel"><div className="inventory-toolbar"><label>Search items<input aria-label="Search items" placeholder="Search by name or SKU" value={search} onChange={e => setSearch(e.target.value)}/></label><label>Category<select aria-label="Category filter" value={category} onChange={e => setCategory(e.target.value)}>{['All categories', ...new Set([...categories, ...items.map(i => i.category)])].map(c => <option key={c}>{c}</option>)}</select></label><span className="inventory-count">{filtered.length} of {rows.length} items</span></div>
    <div className="table-scroll"><table><thead><tr><th>Item / SKU</th><th>Category</th><th>On hand</th><th>On order</th><th>Reserved</th><th>Reorder point</th><th>Position</th><th>Status</th><th>Unit cost</th><th>Action</th></tr></thead><tbody>{filtered.map(item => <tr key={item.id}><td><strong>{item.name}</strong><small>{item.sku} · {item.unit}</small></td><td>{item.category}</td><td>{item.on_hand}</td><td>{item.onOrder}</td><td>{item.reserved_qty}</td><td>{Number(item.reorder_point).toFixed(1)}</td><td>{item.position}</td><td><span className={item.low ? 'stock-pill low' : 'stock-pill healthy'}>{item.low ? 'Low Stock' : 'Healthy'}</span></td><td>{rupees.format(Number(item.unit_cost))}</td><td><button className="button-light" onClick={() => openForm(item)}>Edit</button></td></tr>)}{!loading && filtered.length === 0 && <tr><td colSpan="10" className="empty">No matching inventory items.</td></tr>}</tbody></table></div>{loading && <p className="subtle">Loading inventory and purchase orders…</p>}</section>
    {editing !== null && <div className="modal-backdrop"><section className="item-dialog" role="dialog" aria-modal="true" aria-label={editing === 'new' ? 'Add item' : 'Edit item'}><h2>{editing === 'new' ? 'Add inventory item' : 'Edit item details'}</h2><form onSubmit={save}>{[['name','Item name'],['sku','SKU'],['category','Category'],['unit','Unit'],['unit_cost','Unit cost (INR)']].map(([key,label]) => <label key={key}>{label}{key === 'category' ? <><input list="shelfsync-categories" required maxLength="100" value={form[key]} onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}/><datalist id="shelfsync-categories">{categories.map(c => <option key={c} value={c}/>)}</datalist></> : <input required maxLength={key === 'name' ? 200 : key === 'sku' ? 64 : 40} type={key === 'unit_cost' ? 'number' : 'text'} min={key === 'unit_cost' ? 0 : undefined} step={key === 'unit_cost' ? '.01' : undefined} value={form[key]} onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}/>}</label>)}
    {initialOrderQuantity !== null && <label>
      First order quantity (optional)
      <input type="number" min="1" max="2147483647" step="1" value={initialOrderQuantity}
        onChange={e => setInitialOrderQuantity(e.target.value)} />
      <span className="subtle">Enter the number of units to request in Purchasing.</span>
    </label>}
    <label className="full">Description<textarea maxLength="5000" rows="3" value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))}/></label>{error && <p className="error full" role="alert">{error}</p>}<div className="dialog-actions"><button type="button" className="button-light" onClick={() => setEditing(null)} disabled={saving}>Cancel</button><button className="button-primary" disabled={saving}>{saving ? 'Saving…' : 'Save item'}</button></div></form></section></div>}
    </div></main></div>
}
