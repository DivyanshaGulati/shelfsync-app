export function availableCheckoutStock(item) {
  return Math.max(0, Number(item.on_hand) - Number(item.reserved_qty))
}

export function addCheckoutItem(cart, item, quantity, note) {
  const existing = cart.find(line => line.item.id === item.id)
  if (existing?.attempted) throw new Error('Retry this checkout before changing the item.')

  const combinedQuantity = (existing?.quantity ?? 0) + quantity
  if (!Number.isSafeInteger(quantity) || quantity < 1) throw new Error('Enter a positive whole quantity.')
  if (combinedQuantity > availableCheckoutStock(item)) throw new Error('The combined quantity exceeds available stock.')

  const combinedNote = [existing?.note, note.trim()].filter(Boolean).join('\n')
  if (combinedNote.length > 5000) throw new Error('The combined usage note exceeds 5,000 characters.')
  if (existing) {
    return cart.map(line => line === existing
      ? { ...line, item, quantity: combinedQuantity, note: combinedNote || null }
      : line)
  }

  if (!globalThis.crypto?.randomUUID) throw new Error('Secure checkout requires a browser with crypto.randomUUID support.')
  const group = cart[0]?.group ?? crypto.randomUUID()
  return [...cart, { item, quantity, note: combinedNote || null, group, key: `checkout-cart-${group}-${crypto.randomUUID()}` }]
}
