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
const numberFormat = new Intl.NumberFormat('en-IN')

export default function InventoryInsightsPage() {
  const { token, user, logout } = useAuth()
  const owner = Boolean(token && user?.role === 'OWNER')
  const [stock, setStock] = useState(null)
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
    requestJSON(`${insightsUrl}/snapshot`, options).then(snapshot => {
      if (!controller.signal.aborted) setStock(snapshot)
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
      setStock(report)
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
  const items = stock?.priority_items ?? []
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

          {loading && <p className="insights-feedback" role="status">Loading inventory…</p>}
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
              {tip?.products ? <>
                <p><strong>{tip.primary_share}% of {tip.products[0]} checkouts included {tip.products[1]}</strong> in the last {tip.window_days} days ({tip.together} of {tip.primary_total}).</p>
                <p>
                  {tip.action === 'test_bundle' ? 'Try a bundle' : 'Stock them side by side'}
                  {tip.target_share == null ? ' and track the rate next week.' : <> and aim for <strong>{tip.target_share}% next week</strong>.</>}
                </p>
              </> : tip ? <>
                <p><strong>{tip.product}</strong> · {numberFormat.format(tip.available)} available.
                  {tip.weekly_units > 0
                    ? <> <strong>{numberFormat.format(tip.weekly_units)} checked out</strong> in the last 7 days.</>
                    : tip.units_checked_out > 0 && <> {numberFormat.format(tip.units_checked_out)} checked out in {tip.window_days} days.</>}
                </p>
                <p>
                  {tip.action === 'review_stock' ? 'Check incoming orders before topping up.' : 'Move it to a more visible spot'}
                  {tip.action === 'promote_item' && (tip.weekly_target == null
                    ? ' and track checkouts next week.'
                    : <> and aim for <strong>{numberFormat.format(tip.weekly_target)} units next week</strong>.</>)}
                </p>
              </> : <p><Link to="/inventory">Add inventory items</Link> to get stock and display suggestions.</p>}
            </section>
          )}

          {stock && (
            <section className="panel insights-report" aria-labelledby="insight-report-heading">
              <h2 id="insight-report-heading" className="section-title">
                <span className="icon-tile"><Icon name="list" /></span>
                Priority Items ({numberFormat.format(items.length)})
              </h2>
              <InventoryInsight items={items} summary={stock.summary} />
            </section>
          )}
        </div>
      </main>
    </div>
  )
}
