import { useCallback, useEffect, useState } from 'react'

interface UseApiResourceResult<T> {
  data: T | undefined
  loading: boolean
  error: string | null
  reload: () => void
}

export function useApiResource<T>(
  fetchFn: () => Promise<T>,
  deps: unknown[] = [],
  pollMs: number | null = null,
): UseApiResourceResult<T> {
  const [data, setData] = useState<T | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const fetchOnce = useCallback((showLoading: boolean) => {
    if (showLoading) setLoading(true)
    setError(null)
    fetchFn()
      .then((result) => setData(result))
      .catch(() => setError('Failed to load data from the incident-agent API.'))
      .finally(() => { if (showLoading) setLoading(false) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => {
    fetchOnce(true)
    if (!pollMs) return undefined
    const interval = setInterval(() => fetchOnce(false), pollMs)
    return () => clearInterval(interval)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return { data, loading, error, reload: () => fetchOnce(true) }
}
