import { Link } from 'react-router-dom'

const formatQuantity = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 4 })
const orderLabels = {
  PLACED: 'Order placed',
  IN_TRANSIT: 'In transit',
  PARTIALLY_RECEIVED: 'Partially received',
}

export default function InventoryInsight({ insight }) {
  const { inventory_items: total, low_stock_items: lowStock, priority_item: item } = insight
  const headline = total === 0 ? 'No inventory items yet'
    : lowStock === 0 ? 'No low-stock items'
    : lowStock === 1 ? '1 item needs attention'
    : lowStock > 1 ? `${formatQuantity.format(lowStock)} items need attention` : null

  return (
    <div className="inventory-insight" role="status">
      {headline && (
        <p className={lowStock > 0 ? 'insight-headline insight-headline-low' : 'insight-headline'}>
          {headline}
        </p>
      )}
      <p className="insight-summary">{insight.summary}</p>
      {item ? (
        <div className="insight-priority">
          <div className="insight-priority-header">
            <div>
              <span className="insight-priority-label">Priority item</span>
              <h3>{item.name}</h3>
              <small>{item.sku}</small>
            </div>
            <span className="stock-pill low">Low Stock</span>
          </div>
          <dl className="insight-stock">
            <div><dt>On hand</dt><dd>{formatQuantity.format(item.on_hand)}</dd></div>
            <div><dt>Reorder point</dt><dd>{formatQuantity.format(item.reorder_point)}</dd></div>
            <div><dt>On order</dt><dd>{formatQuantity.format(item.on_order)}</dd></div>
          </dl>
          {item.reserved_qty > 0 && (
            <p className="insight-detail">
              {formatQuantity.format(item.reserved_qty)} reserved · {formatQuantity.format(item.available_stock)} available
            </p>
          )}
          <div className="insight-next">
            <p>{orderLabels[item.order_status] || 'No stock on order'}</p>
            <Link to="/purchasing">{item.on_order > 0 ? 'Track the order' : 'Check purchase requests'}</Link>
          </div>
        </div>
      ) : insight.priority_sku && <p className="insight-detail">Priority SKU <strong>{insight.priority_sku}</strong></p>}
    </div>
  )
}
