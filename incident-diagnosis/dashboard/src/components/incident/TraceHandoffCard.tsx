import type { TraceHandoff } from '../../types/incident'

export function TraceHandoffCard({ trace }: { trace?: TraceHandoff }) {
  const value = trace ?? { status: 'unavailable' as const }
  const linked = value.status === 'correlated' && /^https?:\/\//.test(value.grafana_url ?? '')
  return <section className="rounded-lg border border-line bg-white p-4 shadow-sm">
    <h3 className="font-semibold text-ink">Trace handoff</h3>
    <p className="mt-1 text-sm text-muted">Status: <span className="font-medium text-ink">{value.status.replace('_', ' ')}</span></p>
    {value.trace_id && <p className="mt-1 break-all font-mono text-xs text-muted">{value.trace_id}</p>}
    {value.error_service && <p className="mt-2 text-sm text-ink">{value.error_service} / {value.error_operation}</p>}
    {linked && <a className="mt-3 inline-block text-sm font-medium text-blue-700 underline" href={value.grafana_url} target="_blank" rel="noreferrer">Open complete trace</a>}
    {!linked && value.status !== 'correlated' && <p className="mt-2 text-xs text-muted">No exact causal trace link is available; verify telemetry manually.</p>}
  </section>
}
