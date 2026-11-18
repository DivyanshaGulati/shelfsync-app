import { NavLink } from 'react-router-dom'

const ownerLinks = [
  { to: '/', icon: '▦', label: 'Overview' },
  { to: '/insights', icon: '✦', label: 'AI Inventory Insights' },
  { to: '/inventory', icon: '▤', label: 'Inventory' },
  { to: '/vendors', icon: '▧', label: 'Vendors' },
  { to: '/purchasing', icon: '▣', label: 'Purchasing' },
]

export default function Sidebar({ employee = false, showContact = false }) {
  const checkout = { to: '/checkout', icon: '▣', label: employee ? 'Employee Checkout' : 'Checkout' }
  const links = employee ? [checkout] : [...ownerLinks, checkout]

  return (
    <aside className="sidebar">
      <div className="brand"><span className="brand-icon" aria-hidden="true">▥</span> ShelfSync</div>
      <div className="nav-heading" id="workspace-navigation">Workspace</div>
      <nav className="sidebar-nav" aria-labelledby="workspace-navigation">
        {links.map(({ to, icon, label }) => (
          <NavLink key={to} to={to} end className="nav-item">
            <span className="nav-icon" aria-hidden="true">{icon}</span>
            <span>{label}</span>
          </NavLink>
        ))}
        {showContact && (
          <NavLink to="/contact" end className="nav-item sidebar-contact">
            <span className="nav-icon" aria-hidden="true">✉︎</span>
            <span>Contact</span>
          </NavLink>
        )}
      </nav>
    </aside>
  )
}
