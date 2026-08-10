export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return ''
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

export function formatTimestamp(ts: number | string | null | undefined): string {
  if (ts === null || ts === undefined || ts === '') return 'Time unavailable'
  const date = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts)
  return Number.isNaN(date.getTime()) ? 'Time unavailable' : date.toLocaleString()
}
