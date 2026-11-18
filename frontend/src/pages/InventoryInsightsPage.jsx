import Icon from '../components/Icon.jsx'
import PageTitle from '../components/PageTitle.jsx'
import { useEffect, useRef, useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import InventoryInsight from '../components/InventoryInsight.jsx'
import Sidebar from '../components/Sidebar.jsx'
import { useAuth } from '../lib/AuthContext.jsx'
import { requestJSON } from '../lib/api.js'

const insightsUrl = '/api/inventory/ai-insights'
const emptyHistory = { items: [], next_before_id: null }
const dateFormat = new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium', timeStyle: 'medium' })
const numberFormat = new Intl.NumberFormat('en-IN')

export default function InventoryInsightsPage() {
  const { token, user, logout } = useAuth()
  const owner = Boolean(token && user?.role === 'OWNER')
  const [stock, setStock] = useState(null)
  const [history, setHistory] = useState(emptyHistory)
  const [selectedId, setSelectedId] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [actionError, setActionError] = useState('')
  const [pending, setPending] = useState('')
  const [reload, setReload] = useState(0)
  const inFlight = useRef(null)

  useEffect(() => {
    if (!owner) return
    const controller = new AbortController()
    const options = { token, signal: controller.signal }
    setLoading(true)
    setLoadError('')
    Promise.all([
      requestJSON(`${insightsUrl}/snapshot`, options),
      requestJSON(insightsUrl, options),
    ]).then(([snapshot, reports]) => {
      if (controller.signal.aborted) return
      setStock(snapshot)
      setHistory(reports)
      setSelectedId(reports.items[0]?.id ?? null)
    }).catch(error => {
      if (controller.signal.aborted) return
      if (error.status === 401) logout()
      else setLoadError('Could not load inventory insights. Please try again.')
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })
    return () => {
      controller.abort()
      inFlight.current?.abort()
    }
  }, [owner, token, reload, logout])

  async function run(action, work) {
    if (loading || inFlight.current) return
    const controller = new AbortController()
    inFlight.current = controller
    setPending(action)
    setActionError('')
    try {
      await work({ token, signal: controller.signal })
    } catch (error) {
      if (controller.signal.aborted) return
      if (error.status === 401) logout()
      else setActionError(action === 'generate'
        ? 'Could not generate an insight right now. Please try again.'
        : 'Could not load earlier insights. Please try again.')
    } finally {
      if (inFlight.current === controller) inFlight.current = null
      if (!controller.signal.aborted) setPending('')
    }
  }

  function generate() {
    run('generate', async options => {
      const report = await requestJSON(insightsUrl, { ...options, method: 'POST' })
      if (options.signal.aborted) return
      setHistory(current => ({ ...current, items: [report, ...current.items] }))
      setSelectedId(report.id)
      setStock({ inventory_items: report.inventory_items, low_stock_items: report.low_stock_items })
    })
  }

  function loadEarlier() {
    run('history', async options => {
      const older = await requestJSON(`${insightsUrl}?before_id=${history.next_before_id}`, options)
      if (options.signal.aborted) return
      setHistory(current => ({ ...older, items: [...current.items, ...older.items] }))
    })
  }

  if (!owner) return <Navigate to={token && user?.role === 'EMPLOYEE' ? '/checkout' : '/'} replace />
  const selected = history.items.find(report => report.id === selectedId)
  const busy = loading || Boolean(pending)
  return (
    <div className="dashboard-shell">
      <Sidebar />
      <main className="dashboard-main">
        <header className="topbar">
          <span>Operations / <strong>AI Inventory Insights</strong></span>
          <div className="topbar-actions">
            <span>{user.name} · OWNER</span>
            <button className="button-light" onClick={logout}>Sign out</button>
          </div>
        </header>
        <div className="content insights-page">
          <section className="heading-row">
            <PageTitle icon="/insights" title="AI Inventory Insights"
              subtitle="See what needs restocking and what’s already on the way." />
            <button className="button-primary" onClick={generate} disabled={busy}>
              {pending === 'generate' ? 'Generating insight…' : 'Generate insight'}
            </button>
          </section>

          {loading && <p className="insights-feedback" role="status">Loading inventory and saved insights…</p>}
          {loadError && <div className="error-banner insights-feedback" role="alert">
            <p>{loadError}</p>
            <button className="button-light" disabled={busy} onClick={() => setReload(value => value + 1)}>Try again</button>
          </div>}
          {actionError && <p className="error-banner insights-feedback" role="alert">{actionError}</p>}
          {pending === 'generate' && <p className="insights-feedback" role="status">Reviewing stock and incoming orders…</p>}

          {stock && <div className="insight-highlights">
            <article className="amber"><h2 className="section-title"><Icon name="alert" /> {numberFormat.format(stock.low_stock_items)} Stock Alerts</h2>
              <p>Items flagged by the current stock check.</p><Link to="/inventory">View inventory →</Link>
            </article>
            <article className="green"><h2 className="section-title"><Icon name="/vendors" /> Compare supplier prices</h2>
              <p>Compare sample prices and delivery times.</p><Link to="/vendors">Explore quotes →</Link>
            </article>
            <article className="violet"><h2 className="section-title"><Icon name="/insights" /> Inventory guidance</h2>
              <p>See which item to review first and why.</p><button type="button" onClick={() => document.getElementById('insight-report-heading')?.focus()}>Explore insight →</button>
            </article>
          </div>}

          <div className="insights-grid">
            <section className="panel insights-report" aria-labelledby="insight-report-heading">
              <h2 id="insight-report-heading" tabIndex={-1} className="section-title"><Icon name="layers" /> {selected ? 'Saved insight' : 'Inventory report'}</h2>
              {selected ? (
                <>
                  <p className="subtle insights-date">
                    Stock as of <time dateTime={selected.created_at}>{dateFormat.format(new Date(selected.created_at))}</time>
                  </p>
                  <InventoryInsight insight={selected} />
                </>
              ) : !loading && !loadError && (
                <p className="insights-empty">Generate an insight to see which item to review first and why.</p>
              )}
            </section>

            <section className="panel insights-history" aria-labelledby="insights-history-heading">
              <h2 id="insights-history-heading" className="section-title"><Icon name="clock" /> Insight History</h2>
              {history.items.length > 0 ? (
                <ul className="insights-history-list">
                  {history.items.map(report => (
                    <li key={report.id}>
                      <button type="button" className="insights-history-item" aria-pressed={report.id === selectedId}
                        onClick={() => setSelectedId(report.id)}>
                        <time dateTime={report.created_at}>{dateFormat.format(new Date(report.created_at))}</time>
                        <span>{report.low_stock_items === 0 ? 'No low-stock items' : `${numberFormat.format(report.low_stock_items)} low-stock`}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              ) : !loading && !loadError && <p className="insights-empty">Generated insights will appear here.</p>}
              {history.next_before_id && (
                <button className="button-light insights-more" onClick={loadEarlier} disabled={busy}>
                  {pending === 'history' ? 'Loading…' : 'Load earlier insights'}
                </button>
              )}
            </section>
          </div>
        </div>
      </main>
    </div>
  )
}
