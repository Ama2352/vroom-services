export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return ''
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

export function formatTimestamp(ts: number): string {
  return new Date(ts * 1000).toLocaleString()
}
