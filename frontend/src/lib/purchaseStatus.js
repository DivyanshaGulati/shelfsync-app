export const COMPLETED_PURCHASE_STATUSES = ['RECEIVED', 'REJECTED']

export const purchaseStatusGroups = [
  { key: 'quotes', label: 'Waiting for Quotes', statuses: ['WAITING_FOR_QUOTES'] },
  { key: 'approval', label: 'Pending Approval', statuses: ['PENDING_APPROVAL'] },
  { key: 'placed', label: 'Order Placed', statuses: ['PLACED'] },
  { key: 'transit', label: 'In Transit', statuses: ['IN_TRANSIT', 'PARTIALLY_RECEIVED'] },
  { key: 'completed', label: 'Completed', statuses: COMPLETED_PURCHASE_STATUSES },
]

export function matchesPurchaseStatus(order, group) {
  return group.statuses.includes(order.tracking_status || order.status)
}

const onOrderStatuses = new Set(['PLACED', 'IN_TRANSIT', 'PARTIALLY_RECEIVED'])

export function inventoryRows(items, orders) {
  const outstanding = new Map()
  for (const order of orders) {
    if (!onOrderStatuses.has(order.status)) continue
    const remaining = Math.max(0, Number(order.ordered_quantity) - Number(order.received_quantity))
    outstanding.set(order.inventory_item_id, (outstanding.get(order.inventory_item_id) || 0) + remaining)
  }
  return items.map(item => {
    const onOrder = outstanding.get(item.id) || 0
    const availableStock = Number(item.on_hand) - Number(item.reserved_qty)
    const position = availableStock + onOrder
    return { ...item, onOrder, position, availableStock, low: availableStock <= Number(item.reorder_point) }
  })
}
