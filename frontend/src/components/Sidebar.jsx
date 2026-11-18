import Icon from './Icon.jsx'
import { NavLink } from 'react-router-dom'

const ownerLinks = [
  { to: '/', label: 'Overview' },
  { to: '/insights', label: 'AI Inventory Insights' },
  { to: '/inventory', label: 'Inventory' },
  { to: '/vendors', label: 'Vendors' },
  { to: '/purchasing', label: 'Purchasing' },
]

export default function Sidebar({ employee = false }) {
  const checkout = { to: '/checkout', label: employee ? 'Employee Checkout' : 'Checkout' }
  const links = employee ? [checkout] : [...ownerLinks, checkout]

  return (
    <aside className="sidebar">
      <div className="brand"><span className="brand-icon" aria-hidden="true"><Icon name="/inventory" /></span> ShelfSync</div>
      <div className="nav-heading" id="workspace-navigation">Workspace</div>
      <nav className="sidebar-nav" aria-labelledby="workspace-navigation">
        {links.map(({ to, label }) => (
          <NavLink key={to} to={to} end className="nav-item">
            <Icon name={to} />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
    </aside>
  )
}
