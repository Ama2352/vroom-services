import { Activity, AlertTriangle, GitBranch, Server } from 'lucide-react'
import type { OccurrenceEvidence } from '../../types/incident'
import { Card, CardTitle } from '../ui/Card'

function Row({ label, value }: { label: string; value: string | number | null | undefined }) {
  if (value === null || value === undefined || value === '') return null
  return <div className="flex justify-between gap-3 border-t border-border py-1.5 text-xs first:border-0 first:pt-0"><dt className="text-ink-soft">{label}</dt><dd className="break-words text-right font-mono text-ink">{String(value)}</dd></div>
}

function FailureSection({ evidence }: { evidence: OccurrenceEvidence }) {
  const impact = evidence.impact
  const log = evidence.log_evidence
  const trace = evidence.trace_handoff
  if (!impact && !log && !trace) return null
  return <Card><CardTitle><Activity size={14} /> Failure and impact</CardTitle><dl className="m-0">
    <Row label="Impact status" value={impact?.status} />
    <Row label="Request rate" value={impact?.request_rate} />
    <Row label="HTTP error rate" value={impact?.error_rate_percent === null ? 'No data' : impact?.error_rate_percent} />
    <Row label="Structured log" value={log?.status === 'found' ? log.message : undefined} />
    <Row label="Trace" value={trace?.status === 'correlated' ? `${trace.trace_id || 'linked'}${trace.grafana_url ? ' ↗' : ''}` : undefined} />
    {trace && trace.status !== 'correlated' && <div className="border-t border-border pt-1.5 text-xs text-ink-faint"><strong className="text-root-cause-label">Trace unavailable:</strong> {trace.status.replaceAll('_', ' ')}</div>}
  </dl></Card>
}

function RuntimeSection({ evidence }: { evidence: OccurrenceEvidence }) {
  const hasRuntime = evidence.pods_desired !== undefined || evidence.pods_ready !== undefined || evidence.waiting_reason
  if (!hasRuntime) return null
  return <Card><CardTitle><Server size={14} /> Runtime and Kubernetes</CardTitle><dl className="m-0">
    <Row label="Ready pods" value={evidence.pods_ready !== undefined && evidence.pods_desired !== undefined ? `${evidence.pods_ready} / ${evidence.pods_desired}` : undefined} />
    <Row label="Waiting reason" value={evidence.waiting_reason} />
    <Row label="Restarts" value={evidence.restarts as number | undefined} />
  </dl></Card>
}

function ChangeSection({ evidence }: { evidence: OccurrenceEvidence }) {
  const diff = evidence.template_diff
  const provenance = evidence.provenance
  if (!diff && !provenance) return null
  const env = diff?.env_diff?.[0]
  return <Card><CardTitle><GitBranch size={14} /> Configuration and provenance</CardTitle><dl className="m-0">
    <Row label="Environment change" value={env ? `${env.key}: ${env.old_value} → ${env.new_value}` : undefined} />
    <Row label="Image change" value={diff?.image_changed ? `${diff.old_image || 'unknown'} → ${diff.new_image || 'unknown'}` : undefined} />
    <Row label="Provenance" value={provenance && 'dual' in provenance ? provenance.causal_status.status.replaceAll('_', ' ') : provenance?.classification} />
  </dl></Card>
}

function DependencySection({ evidence }: { evidence: OccurrenceEvidence }) {
  const dependency = evidence.dependency
  if (!dependency) return null
  return <Card className={dependency.pods_available === dependency.pods_desired && !dependency.waiting_reason ? '' : 'border-critical bg-critical-soft'}><CardTitle><AlertTriangle size={14} /> Dependency</CardTitle><dl className="m-0">
    <Row label="Name" value={`${dependency.namespace}/${dependency.name}`} />
    <Row label="Ready pods" value={`${dependency.pods_available} / ${dependency.pods_desired}`} />
    <Row label="Waiting reason" value={dependency.waiting_reason} />
  </dl></Card>
}

export function EvidenceSections({ evidence }: { evidence: OccurrenceEvidence }) {
  return <div className="grid gap-3 md:grid-cols-2"><FailureSection evidence={evidence} /><RuntimeSection evidence={evidence} /><ChangeSection evidence={evidence} /><DependencySection evidence={evidence} /></div>
}
