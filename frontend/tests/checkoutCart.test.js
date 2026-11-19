import assert from 'node:assert/strict'
import test from 'node:test'
import { addCheckoutItem } from '../src/lib/checkoutCart.js'

const blade = { id: 1, on_hand: 45, reserved_qty: 3 }
const screw = { id: 2, on_hand: 10, reserved_qty: 0 }

test('adds repeat quantities to the same checkout row and keeps both notes', () => {
  const original = addCheckoutItem([], blade, 10, 'First job')
  const cart = addCheckoutItem(original, screw, 1, '')
  const merged = addCheckoutItem(cart, blade, 32, 'Second job')

  assert.equal(merged.length, 2)
  assert.equal(merged[0].quantity, 42)
  assert.equal(merged[0].note, 'First job\nSecond job')
  assert.equal(merged[0].key, original[0].key)
  assert.equal(merged[0].group, merged[1].group)
  assert.equal(original[0].quantity, 10)
})

test('rejects totals above unreserved stock and invalid quantities', () => {
  const cart = addCheckoutItem([], blade, 10, '')
  assert.throws(() => addCheckoutItem(cart, blade, 33, ''), /exceeds available stock/)
  assert.throws(() => addCheckoutItem(cart, blade, 1.5, ''), /whole quantity/)
  assert.throws(() => addCheckoutItem(cart, blade, 0, ''), /positive/)
})

test('checks the merged usage note against the API limit', () => {
  const cart = addCheckoutItem([], blade, 1, 'a'.repeat(4999))
  assert.throws(() => addCheckoutItem(cart, blade, 1, 'b'), /5,000 characters/)
  assert.equal(addCheckoutItem(cart, blade, 1, '')[0].note.length, 4999)
})

test('prevents editing an item with an unconfirmed checkout response', () => {
  const [line] = addCheckoutItem([], blade, 1, '')
  assert.throws(() => addCheckoutItem([{ ...line, attempted: true }], blade, 1, ''), /Retry this checkout/)
})
