import ProductIdentity from './ProductIdentity.jsx'
import { Link } from 'react-router-dom'

const orderLabels = {
  PLACED: 'Order Placed',
  IN_TRANSIT: 'In Transit',
  PARTIALLY_RECEIVED: 'Partially Received',
}

export default function InventoryInsight({ items, insight }) {
  return (
    <>
      {items.length > 0 ? (
        <div className="table-scroll insights-table">
          <table aria-label="Priority items">
            <thead><tr><th scope="col">Item / SKU</th><th scope="col">Low Stock</th><th scope="col">Status</th></tr></thead>
            <tbody>{items.map(item => (
              <tr key={item.sku}>
                <td><ProductIdentity name={item.name} sku={item.sku} /></td>
                <td><span className="stock-pill low">Low Stock</span></td>
                <td>
                  <Link className={item.on_order > 0 ? 'stock-pill insights-incoming' : 'stock-pill low'} to="/purchasing">
                    {orderLabels[item.order_status] || (item.on_order > 0 ? 'On Order' : 'Action Needed')}
                  </Link>
                </td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      ) : <p className="insights-empty">No low-stock items.</p>}
      {insight && (
        <details className="insights-summary">
          <summary>AI summary</summary>
          <p>{insight.summary}</p>
        </details>
      )}
    </>
  )
}
