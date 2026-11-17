import assert from 'node:assert/strict'
import test from 'node:test'
import { inventoryRows } from '../src/lib/purchaseStatus.js'

const pvcElbow = { id: 1, on_hand: 4, reserved_qty: 0, reorder_point: '108.0' }
const purchase = { inventory_item_id: 1, ordered_quantity: 104, received_quantity: 0 }

test('placed and in-transit orders stay in inventory position but do not clear Low Stock', () => {
  for (const status of ['PLACED', 'IN_TRANSIT']) {
    // This outstanding order takes inventory position above ROP.
    const [row] = inventoryRows([pvcElbow], [{ ...purchase, ordered_quantity: 120, status }])
    assert.equal(row.onOrder, 120)
    assert.equal(row.position, 124)
    assert.equal(row.availableStock, 4)
    assert.equal(row.low, true)
  }
})

test('fully received stock equal to ROP stays low and stock above ROP becomes healthy', () => {
  const [placed] = inventoryRows([pvcElbow], [{ ...purchase, status: 'PLACED' }])
  assert.equal(placed.position, 108)
  assert.equal(placed.low, true)

  const received = { ...purchase, status: 'RECEIVED', received_quantity: 104 }
  const [atThreshold, aboveThreshold] = inventoryRows([
    { ...pvcElbow, on_hand: 108 },
    { ...pvcElbow, id: 2, on_hand: 109 },
  ], [received])
  assert.equal(atThreshold.onOrder, 0)
  assert.equal(atThreshold.low, true)
  assert.equal(aboveThreshold.low, false)
})

test('reservations reduce available stock and only unreceived units remain on order', () => {
  const [row] = inventoryRows(
    [{ ...pvcElbow, on_hand: 110, reserved_qty: 2 }],
    [{ ...purchase, status: 'PARTIALLY_RECEIVED', received_quantity: 100 }],
  )
  assert.equal(row.availableStock, 108)
  assert.equal(row.onOrder, 4)
  assert.equal(row.position, 112)
  assert.equal(row.low, true)
})
