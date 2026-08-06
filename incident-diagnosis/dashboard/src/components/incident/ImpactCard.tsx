import type { ImpactEvidence } from '../../types/incident'
import { Card, CardTitle } from '../ui/Card'

function value(value: number | null | undefined, suffix = '') {
  return value === null || value === undefined ? 'No data' : `${value.toFixed(2)}${suffix}`
}

export function ImpactCard({ impact }: { impact?: ImpactEvidence }) {
  if (!impact) return null
  const metric = impact.triggering_metric
  return <Card>
    <CardTitle>Impact evidence</CardTitle>
    <dl className="m-0 space-y-1 text-xs text-ink-soft">
      <div className="flex justify-between gap-3"><dt>Status</dt><dd className="font-mono text-ink">{impact.status.replace('_', ' ')}</dd></div>
      {metric && <div className="flex justify-between gap-3"><dt>{metric.name}</dt><dd className="font-mono text-ink">{value(metric.value)}{metric.threshold !== null ? ` (threshold ${value(metric.threshold)})` : ''}</dd></div>}
      <div className="flex justify-between gap-3"><dt>Request rate</dt><dd className="font-mono text-ink">{value(impact.request_rate, '/s')}</dd></div>
      <div className="flex justify-between gap-3"><dt>HTTP error rate</dt><dd className="font-mono text-ink">{value(impact.error_rate_percent, '%')}</dd></div>
      <div className="flex justify-between gap-3"><dt>p99 latency</dt><dd className="font-mono text-ink">{value(impact.p99_seconds, 's')}</dd></div>
    </dl>
  </Card>
}
