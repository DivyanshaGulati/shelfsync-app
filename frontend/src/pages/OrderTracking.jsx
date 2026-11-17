const labels = { PLACED: 'Order Placed', IN_TRANSIT: 'In Transit', RECEIVED: 'Delivered' }

export function trackingLabel(order) {
  return labels[order.tracking_status]
}

export default function OrderTracking({ order, disabled, onTransit, onDelivery }) {
  const days = order.expected_delivery_days == null ? null : Number(order.expected_delivery_days)
  return <>
    {order.vendor_name && <p className="subtle">Selected vendor · {order.vendor_name}</p>}
    {(days != null || order.tracking_status) && <p className="subtle">
      Expected delivery · {days == null ? 'Estimate unavailable' : `${days} ${days === 1 ? 'day' : 'days'} from placement`}
    </p>}
    {order.tracking_status === 'PLACED' &&
      <button className="button-primary" disabled={disabled} onClick={onTransit}>Mark as In Transit</button>}
    {order.tracking_status === 'IN_TRANSIT' &&
      <button className="button-primary" disabled={disabled} onClick={onDelivery}>Mark as Delivered</button>}
  </>
}
