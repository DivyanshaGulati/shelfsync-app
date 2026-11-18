import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Area, AreaChart, CartesianGrid, Cell, Pie, PieChart, ReferenceDot, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import Icon from './Icon.jsx'
import PageTitle from './PageTitle.jsx'
import { inventoryRows, matchesPurchaseStatus, purchaseStatusGroups } from '../lib/purchaseStatus.js'

const money = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 })
const axis = new Intl.NumberFormat('en-IN', { notation: 'compact', maximumFractionDigits: 1 })
const date = new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', timeZone: 'UTC' })
const colors = { quotes: '#145fea', approval: '#75afff', placed: '#a58bed', transit: '#17365d', completed: '#5293e5' }

export default function OwnerOverview({ data, loading, error, onRefresh }) {
  const [days, setDays] = useState(14)
  const inventory = data ? inventoryRows(data.inventory, data.orders) : []
  const value = inventory.reduce((total, item) => total + Number(item.on_hand) * Number(item.unit_cost), 0)
  const points = (data?.values ?? []).slice(-days).map(point => ({
    day: date.format(new Date(point.date)),
    value: Number(point.value),
  }))
  if (points.length && data.values.at(-1).date === new Date().toISOString().slice(0, 10)) {
    points.at(-1).value = value
  }
  const opening = points[0]?.value
  const latest = points.at(-1)
  const change = opening > 0 ? Math.round(100 * (latest.value - opening) / opening) : null
  const groups = purchaseStatusGroups.map(group => ({
    ...group, color: colors[group.key],
    count: data?.orders.filter(order => matchesPurchaseStatus(order, group)).length ?? 0,
  })).filter(group => group.key !== 'placed' || group.count > 0)
  const count = key => groups.find(group => group.key === key).count

  return <div className="content owner-overview">
    <div className="heading-row">
      <PageTitle icon="/insights" title="Welcome back!" subtitle="Here’s what’s happening with your inventory today." />
      <div className="overview-controls">
        <label className="button-light overview-period">
          <Icon name="calendar" />
          <select aria-label="Inventory trend date range" value={days} onChange={event => setDays(Number(event.target.value))}>
            {[7, 14, 30].map(period => <option key={period} value={period}>Last {period} days</option>)}
          </select>
        </label>
        <button className="button-light overview-refresh" type="button" disabled={loading} onClick={onRefresh}
          aria-label="Refresh dashboard" title="Refresh dashboard"><Icon name="refresh" /></button>
      </div>
    </div>
    {error && <p className="error-banner" role="alert">{error}</p>}
    {data ? <>
      <div className="overview-metrics">
        <Metric label="Inventory Value" icon="/inventory" tone="blue" value={money.format(value)}>
          {change !== null && <small className={change < 0 ? 'overview-decrease' : 'overview-increase'}>
            {change > 0 ? '↑ ' : change < 0 ? '↓ ' : ''}{Math.abs(change)}% vs start of period
          </small>}
        </Metric>
        <Metric label="Low-Stock Items" icon="alert" tone="amber" value={inventory.filter(item => item.low).length}>
          <Link to="/inventory?stock=low">View Details →</Link>
        </Metric>
        <Metric label="Pending Approvals" icon="check" tone="green" value={count('approval')}>
          <Link to="/purchasing?status=approval">View Approvals →</Link>
        </Metric>
        <Metric label="Orders in Transit" icon="truck" tone="violet" value={count('transit')}>
          <Link to="/purchasing?status=transit">Track Orders →</Link>
        </Metric>
      </div>
      <div className="overview-panels">
        <section className="panel" aria-labelledby="overview-trend">
          <PanelHeading id="overview-trend" icon="growth" title="Inventory Trend">
            Last {days} days at today’s unit costs
          </PanelHeading>
          {points.length ? <div className="overview-trend">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={points} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                <defs><linearGradient id="inventory-value-fill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#2061eb" stopOpacity={0.25} />
                  <stop offset="100%" stopColor="#2061eb" stopOpacity={0.015} />
                </linearGradient></defs>
                <CartesianGrid stroke="#e0e9f7" strokeDasharray="4 4" />
                <XAxis dataKey="day" tick={{ fontSize: 11 }} minTickGap={19} tickMargin={8} />
                <YAxis width={46} tick={{ fontSize: 11 }} tickFormatter={value => axis.format(value)} />
                <Tooltip formatter={amount => [money.format(amount), 'Inventory value']} />
                <Area type="monotone" dataKey="value" stroke="#145fea" strokeWidth={3}
                  fill="url(#inventory-value-fill)" isAnimationActive={false} />
                <ReferenceDot x={latest.day} y={latest.value} r={5} fill="#145fea" stroke="#fff" strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          </div> : <p className="overview-trend-empty">Inventory trend could not be loaded. Refresh to try again.</p>}
        </section>
        <section className="panel" aria-labelledby="overview-pipeline">
          <PanelHeading id="overview-pipeline" icon="/purchasing" title="Purchase Pipeline">
            Your purchasing status at a glance
          </PanelHeading>
          <div className="overview-pipeline">
            <div className="overview-donut">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={data.orders.length ? groups : [{ count: 1, color: '#e8effa' }]} dataKey="count"
                    innerRadius="66%" outerRadius="94%" stroke="none" isAnimationActive={false}>
                    {(data.orders.length ? groups : [{ count: 1, color: '#e8effa' }]).map(group =>
                      <Cell key={group.key ?? 'empty'} fill={group.color} />)}
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
              <div className="overview-donut-total"><strong>{data.orders.length}</strong><span>Total orders</span></div>
            </div>
            <ul className="overview-pipeline-list">
              {groups.map(group => <li key={group.key}>
                <span className="overview-status-dot" style={{ background: group.color }} />
                <span>{group.label}</span><strong>{group.count}</strong>
              </li>)}
            </ul>
          </div>
        </section>
      </div>
    </> : !error && <p className="panel loading-panel" role="status">Loading dashboard…</p>}
  </div>
}

function Metric({ label, icon, tone, value, children }) {
  return <section className={`overview-metric ${tone}`}>
    <h2><span className="icon-tile"><Icon name={icon} /></span><span className="overview-metric-label">{label}</span></h2>
    <strong>{value}</strong>
    {children && <div className="overview-metric-action">{children}</div>}
  </section>
}

function PanelHeading({ id, title, icon, children }) {
  return <header className="overview-panel-heading">
    <span className="icon-tile"><Icon name={icon} /></span>
    <div><h2 id={id}>{title}</h2><p>{children}</p></div>
  </header>
}
