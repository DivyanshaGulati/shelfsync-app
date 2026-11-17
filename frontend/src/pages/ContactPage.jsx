import React, { useState } from 'react'
import { Link } from 'react-router-dom'

export default function ContactPage() {
  const [form, setForm] = useState({ name: '', email: '', message: '', website: '' })
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState(false)
  const [error, setError] = useState('')
  const update = e => setForm({ ...form, [e.target.name]: e.target.value })
  async function submit(e) {
    e.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const response = await fetch('/api/contact', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form),
      })
      if (!response.ok) {
        const data = await response.json().catch(() => ({}))
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Please check your details and try again.')
      }
      setSent(true)
    } catch (err) { setError(err.message || 'Delivery could not be confirmed.') }
    finally { setBusy(false) }
  }
  const fieldStyle = { display: 'block', width: '100%', padding: 12, border: '1px solid #cbd5e1', borderRadius: 8, marginTop: 6 }
  return <main style={{ maxWidth: 620, margin: '48px auto', padding: 24 }}>
    <Link to="/">← ShelfSync</Link>
    <h1>Contact ShelfSync</h1>
    <p>Send a question or share feedback with the ShelfSync team.</p>
    {sent ? <p role="status">Your message has been submitted. Thank you for contacting ShelfSync.</p> :
      <form onSubmit={submit} style={{ display: 'grid', gap: 20, marginTop: 24 }}>
        <label>Your name<input style={fieldStyle} name="name" autoComplete="name" required maxLength={100} value={form.name} onChange={update} /></label>
        <label>Your email<input style={fieldStyle} name="email" type="email" autoComplete="email" required maxLength={254} value={form.email} onChange={update} /></label>
        <label>Message<textarea style={fieldStyle} name="message" rows={6} required minLength={10} maxLength={4000} value={form.message} onChange={update} /></label>
        <div hidden aria-hidden="true"><label>Website<input name="website" tabIndex={-1} autoComplete="off" value={form.website} onChange={update} /></label></div>
        {error && <p role="alert" style={{ color: '#b91c1c' }}>{error}</p>}
        <button disabled={busy} type="submit" style={{ padding: 14, background: '#0f766e', color: 'white', border: 0, borderRadius: 8 }}>{busy ? 'Sending…' : 'Send message'}</button>
      </form>}
  </main>
}
