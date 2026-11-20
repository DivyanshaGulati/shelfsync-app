import { useEffect, useRef, useState } from 'react'
import { useAuth } from '../lib/AuthContext.jsx'
import { requestJSON } from '../lib/api.js'

export default function VendorBillPreview({ orderId, disabled }) {
  const { token, logout } = useAuth()
  const dialog = useRef(null)
  const pending = useRef(null)
  const [image, setImage] = useState('')
  const [error, setError] = useState('')

  useEffect(() => () => pending.current?.abort(), [])

  async function open() {
    dialog.current.showModal()
    if (image || pending.current) return
    const controller = new AbortController()
    pending.current = controller
    setError('')
    try {
      const bill = await requestJSON(`/api/purchase-requests/${orderId}/mock-bill`, {
        token, method: 'POST', signal: controller.signal,
      })
      if (!controller.signal.aborted) setImage(bill.image)
    } catch (err) {
      if (controller.signal.aborted) return
      if (err.status === 401) logout()
      else setError(err.message)
    } finally {
      pending.current = null
    }
  }

  return <>
    <button className="button-light" disabled={disabled} onClick={open}>View Bill</button>
    <dialog ref={dialog} className="item-dialog vendor-bill-preview" aria-label={`Vendor bill for order #${orderId}`}>
      <div className="vendor-bill-header">
        <h2>Vendor Bill</h2>
        <button className="button-light" onClick={() => dialog.current.close()}>Close</button>
      </div>
      {error ? <p role="alert">{error}</p> : image
        ? <img src={`data:image/png;base64,${image}`} alt={`Simulated vendor bill for order #${orderId}`} />
        : <p role="status">Loading bill…</p>}
    </dialog>
  </>
}
