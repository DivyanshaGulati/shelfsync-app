import { accountLabel } from '../lib/accountLabel.js'
import Icon from '../components/Icon.jsx'
import PageTitle from '../components/PageTitle.jsx'
import { useEffect, useRef, useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import InventoryInsight from '../components/InventoryInsight.jsx'
import Sidebar from '../components/Sidebar.jsx'
import { useAuth } from '../lib/AuthContext.jsx'
import { requestJSON } from '../lib/api.js'

const insightsUrl = '/api/inventory/ai-insights'
const historyPageSize = 3
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
  const [tip, setTip] = useState(null)
  const [tipOpen, setTipOpen] = useState(false)
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
      requestJSON(`${insightsUrl}?limit=${historyPageSize}`, options),
    ]).then(([snapshot, reports]) => {
      if (controller.signal.aborted) return
      setStock(snapshot)
      setHistory(reports)
      setSelectedId(null)
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
      else setActionError({
        generate: error.message === 'Stock changed during generation. Generate the insight again.'
          ? error.message : 'Could not generate an insight. Try again.',
        history: 'Could not load earlier insights. Try again.',
        tip: 'Could not load the growth tip. Try again.',
      }[action])
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
      setStock(report)
    })
  }

  function loadEarlier() {
    run('history', async options => {
      const older = await requestJSON(`${insightsUrl}?before_id=${history.next_before_id}&limit=${historyPageSize}`, options)
      if (options.signal.aborted) return
      setHistory(current => ({ ...older, items: [...current.items, ...older.items] }))
    })
  }

  function exploreTip() {
    if (tipOpen) {
      setTipOpen(false)
      return
    }
    run('tip', async options => {
      const result = await requestJSON(`${insightsUrl}/growth-tip`, options)
      if (options.signal.aborted) return
      setTip(result.tip)
      setTipOpen(true)
    })
  }

  if (!owner) return <Navigate to={token && user?.role === 'EMPLOYEE' ? '/checkout' : '/'} replace />
  const selected = history.items.find(report => report.id === selectedId)
  const items = selected
    ? (selected.priority_items ?? (selected.priority_item ? [selected.priority_item] : []))
    : (stock?.priority_items ?? [])
  const busy = loading || Boolean(pending)
  return (
    <div className="dashboard-shell">
      <Sidebar />
      <main className="dashboard-main">
        <header className="topbar">
          <span>Operations / <strong>AI Inventory Insights</strong></span>
          <div className="topbar-actions">
            <span>{accountLabel(user)}</span>
            <button className="button-light" onClick={logout}>Sign out</button>
          </div>
        </header>
        <div className="content insights-page">
          <section className="heading-row">
            <PageTitle icon="/insights" title="AI Inventory Insights" />
            <button className="button-primary insights-generate" onClick={generate} disabled={busy}>
              <Icon name="/insights" />
              {pending === 'generate' ? 'Generating…' : 'Generate Insight'}
            </button>
          </section>

          {loading && <p className="insights-feedback" role="status">Loading inventory and saved insights…</p>}
          {loadError && <div className="error-banner insights-feedback" role="alert">
            <p>{loadError}</p>
            <button className="button-light" disabled={busy} onClick={() => setReload(value => value + 1)}>Try again</button>
          </div>}
          {actionError && <p className="error-banner insights-feedback" role="alert">{actionError}</p>}
          {stock && <div className="insight-highlights">
            <article className="amber">
              <h2 className="section-title"><Icon name="alert" /> {numberFormat.format(stock.low_stock_items)} Stock {stock.low_stock_items === 1 ? 'Alert' : 'Alerts'}</h2>
              <Link to="/inventory?stock=low">View Items <span aria-hidden="true">→</span></Link>
            </article>
            <article className="green">
              <h2 className="section-title"><Icon name="savings" /> Smart Savings</h2>
              <Link to="/vendors">Explore Savings <span aria-hidden="true">→</span></Link>
            </article>
            <article className="violet">
              <h2 className="section-title"><Icon name="growth" /> Business Growth Tip</h2>
              <button type="button" disabled={busy} onClick={exploreTip} aria-expanded={tipOpen}>
                {pending === 'tip' ? 'Finding a tip…' : tipOpen ? 'Hide Tip' : 'Explore Tip'} <span aria-hidden="true">→</span>
              </button>
            </article>
          </div>}

          {tipOpen && (
            <section className="panel insights-tip" role="status">
              <h2 className="section-title"><Icon name="growth" /> Business Growth Tip</h2>
              {tip ? <>
                <p><strong>{tip.products.join(' + ')}</strong> appeared together in {tip.together} of {tip.total} tracked checkouts ({tip.share}%) in the last {tip.window_days} days.</p>
                <p>{tip.action === 'test_bundle' ? 'Try offering these items as a bundle and track how often they’re checked out together.' : 'Display these items together and track how often they’re checked out together.'}</p>
              </> : <p>Not enough checkout history yet. Check back after more items are checked out together.</p>}
            </section>
          )}

          <div className="insights-grid">
            <section className="panel insights-report" aria-labelledby="insight-report-heading">
              <h2 id="insight-report-heading" className="section-title"><span className="icon-tile"><Icon name="list" /></span> Priority Items ({numberFormat.format(items.length)})</h2>
              {(selected || stock) && <InventoryInsight items={items} insight={selected} />}
            </section>

            <section className="panel insights-history" aria-labelledby="insights-history-heading">
              <h2 id="insights-history-heading" className="section-title"><span className="icon-tile"><Icon name="clock" /></span> Insight History</h2>
              {history.items.length > 0 ? (
                <ul className="insights-history-list">
                  {history.items.map(report => (
                    <li key={report.id}>
                      <button type="button" className="insights-history-item" aria-pressed={report.id === selectedId}
                        onClick={() => setSelectedId(current => current === report.id ? null : report.id)}>
                        <Icon name="calendar" />
                        <time dateTime={report.created_at}>{dateFormat.format(new Date(report.created_at))}</time>
                        <span aria-hidden="true">›</span>
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
