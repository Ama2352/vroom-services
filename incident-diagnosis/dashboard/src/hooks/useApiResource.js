import { useCallback, useEffect, useState } from 'react'

export function useApiResource(fetchFn, deps = [], pollMs = null) {
  const [data, setData] = useState(undefined)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const fetchOnce = useCallback((showLoading) => {
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
