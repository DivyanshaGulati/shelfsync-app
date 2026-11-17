import { useEffect } from 'react'
import { ApiError, getAllPages } from './api.js'

export function useStockStatusRefresh(token, enabled, onRefresh, onUnauthorized) {
  useEffect(() => {
    if (!token || !enabled) return
    const controller = new AbortController()
    let timer

    async function refresh() {
      try {
        if (document.visibilityState !== 'visible') return
        const [inventory, orders] = await Promise.all([
          getAllPages('/api/inventory', token, controller.signal),
          getAllPages('/api/purchase-requests', token, controller.signal),
        ])
        if (!controller.signal.aborted) onRefresh(inventory, orders)
      } catch (error) {
        if (!controller.signal.aborted && error instanceof ApiError && error.status === 401) onUnauthorized()
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(refresh, 5000)
      }
    }

    timer = window.setTimeout(refresh, 5000)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
  }, [token, enabled, onRefresh, onUnauthorized])
}
