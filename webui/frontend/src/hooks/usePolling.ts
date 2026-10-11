import { useEffect, useRef, useState } from 'react'

const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'cancelled'])

interface HasStatus {
  status?: string
}

/**
 * Polls fetchFn on a fixed cadence until it returns a terminal status, then
 * stops. fetchFn is read through a ref (not a dependency) so callers can
 * pass a fresh closure each render without restarting the poll loop — if
 * the underlying resource identity changes (e.g. a different run id),
 * render the component with `key={id}` so React remounts it instead.
 */
export function usePolling<T extends HasStatus | null | undefined>(fetchFn: () => Promise<T>, intervalMs = 4000) {
  const [data, setData] = useState<T>()
  const [error, setError] = useState<Error | null>(null)
  const fetchFnRef = useRef(fetchFn)
  fetchFnRef.current = fetchFn

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined

    const tick = async () => {
      try {
        const result = await fetchFnRef.current()
        if (cancelled) return
        setData(result)
        setError(null)
        if (result?.status && TERMINAL_STATUSES.has(result.status)) return
      } catch (err) {
        if (!cancelled) setError(err as Error)
      }
      if (!cancelled) {
        timer = window.setTimeout(tick, intervalMs)
      }
    }

    tick()

    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
    }
  }, [intervalMs])

  return { data, error }
}
