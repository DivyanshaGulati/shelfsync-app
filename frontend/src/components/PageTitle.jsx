import Icon from './Icon.jsx'

export default function PageTitle({ icon, title, subtitle }) {
  return <div className="page-title">
    <span className="icon-tile"><Icon name={icon} /></span>
    <div><h1>{title}</h1>{subtitle && <p>{subtitle}</p>}</div>
  </div>
}
