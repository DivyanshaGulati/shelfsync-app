// Same-origin /api calls use the Vite proxy; tokens never go in URLs.
const baseUrl = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')
export class ApiError extends Error {
  constructor(status, detail) { super(detail || `Request failed (${status})`); this.status = status }
}
export async function requestJSON(path, { token, method = 'GET', body, signal } = {}) {
  const response = await fetch(`${baseUrl}${path}`, {
    method, signal,
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = typeof data?.detail === 'string' ? data.detail : data?.detail?.message
    throw new ApiError(response.status, typeof detail === 'string' ? detail : undefined)
  }
  return data
}
export const getJSON = (path, options) => requestJSON(path, options)
// Fetch every page, rather than silently presenting only the first 100 rows.
export async function getAllPages(path, token, signal) {
  const results = []
  for (let offset = 0; ; offset += 100) {
    const page = await requestJSON(`${path}${path.includes('?') ? '&' : '?'}limit=100&offset=${offset}`, { token, signal })
    results.push(...page.items)
    if (results.length >= page.total || page.items.length === 0) return results
  }
}
