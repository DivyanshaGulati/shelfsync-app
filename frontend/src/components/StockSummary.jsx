import Icon from './Icon.jsx'

export default function StockSummary({ entries }) {
  return <dl className="stock-summary">
    {entries.map(([label, value, icon, tone]) => <div key={label} className={tone}>
      <span className="icon-tile"><Icon name={icon} /></span>
      <div><dt>{label}</dt><dd>{value}</dd></div>
    </div>)}
  </dl>
}
