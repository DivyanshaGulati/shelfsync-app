import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getJSON } from '../lib/api'

const apiOrigin = import.meta.env.VITE_API_BASE_URL || (import.meta.env.DEV ? 'http://127.0.0.1:8000' : '')
const apiDocsUrl = `${apiOrigin.replace(/\/$/, '')}/docs`

export default function SetupPage() {
  const [api, setApi] = useState({ state: 'loading', detail: '' })
  const [database, setDatabase] = useState({ state: 'loading', detail: '' })

  useEffect(() => {
    let alive = true
    getJSON('/api/health')
      .then((data) => alive && setApi({ state: data.status === 'ok' ? 'connected' : 'error', detail: data.service }))
      .catch((err) => alive && setApi({ state: 'error', detail: err.message }))
    getJSON('/api/health/db')
      .then((data) => alive && setDatabase({ state: data.status === 'ok' ? 'connected' : 'error', detail: data.database }))
      .catch((err) => alive && setDatabase({ state: 'error', detail: err.message }))
    return () => { alive = false }
  }, [])

  return (
    <main className="mx-auto max-w-3xl px-5 py-16">
      <header className="mb-10">
        <p className="text-xs font-bold uppercase tracking-[.25em] text-blue-600">ShelfSync · System status</p>
        <h1 className="mt-2 text-4xl font-bold">Project connectivity</h1>
        <p className="mt-3 text-slate-600">Check frontend, API and database connectivity.</p>
      </header>
      <section className="space-y-4 rounded-2xl bg-white p-6 shadow-sm">
        <Status title="Frontend (React + Vite + Tailwind)" value={{ state: 'connected', detail: 'Page rendered' }} />
        <Status title="Backend (FastAPI)" value={api} />
        <Status title="Database (PostgreSQL)" value={database} />
      </section>
      <p className="mt-6 text-sm text-slate-600">Backend API docs: <a className="text-blue-700 underline" href={apiDocsUrl} target="_blank" rel="noreferrer">Open API documentation</a></p>
      <p className="mt-4 text-xs text-slate-500">These checks report service connectivity; sign in to use inventory and purchasing.</p>
      <Link className="mt-6 inline-block text-sm text-blue-700 underline" to="/">Refresh route</Link>
    </main>
  )
}

function Status({ title, value }) {
  const ok = value.state === 'connected'
  return (
    <div className="flex flex-col gap-1 border-b border-slate-100 py-3 last:border-0 sm:flex-row sm:items-center sm:justify-between">
      <div className="font-semibold">{title}</div>
      <div role="status" className={`text-sm ${ok ? 'text-emerald-700' : value.state === 'loading' ? 'text-slate-500' : 'text-rose-700'}`}>
        {ok ? '✓ Connected' : value.state === 'loading' ? 'Checking…' : '✕ Not connected'}
        {value.detail && <span className="ml-2 text-slate-500">({value.detail})</span>}
      </div>
    </div>
  )
}
