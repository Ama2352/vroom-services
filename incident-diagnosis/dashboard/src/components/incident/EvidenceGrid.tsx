import { Activity, RefreshCw, AlertTriangle, History } from 'lucide-react'
import type { Incident } from '../../types/incident'
import { Card, CardTitle } from '../ui/Card'

function Row({ label, value }: { label: string; value: string | number | null | undefined }) {
  if (value === '' || value === null || value === undefined) return null
  return (
    <div className="flex justify-between gap-3 py-0.5 text-xs text-ink-soft">
      <dt>{label}</dt>
      <dd className="break-words font-mono text-ink">{String(value)}</dd>
    </div>
  )
}

function DiffBlock({ header, oldValue, newValue }: { header: string; oldValue?: string; newValue?: string }) {
  return (
    <div className="mb-2 last:mb-0">
      <div className="mb-1 font-mono text-xs text-ink-faint">{header}</div>
      <div className="overflow-hidden rounded-md border border-border font-mono text-[11px]">
        <div className="whitespace-pre-wrap break-words bg-critical-soft px-2 py-1 text-critical">- {oldValue}</div>
        <div className="whitespace-pre-wrap break-words bg-healthy-soft px-2 py-1 text-healthy">+ {newValue}</div>
      </div>
    </div>
  )
}

export function EvidenceGrid({ incident }: { incident: Incident }) {
  const td = incident.template_diff
  const dep = incident.dependency
  const hasInit = Boolean(incident.init_waiting_reason || incident.init_last_terminated_reason || incident.init_restarts > 0)
  const hasLogEvent = Boolean(incident.log_error || incident.event_reason)

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Card>
          <CardTitle><Activity size={14} /> Pod Health</CardTitle>
          <dl className="m-0">
            <Row label="Pods available" value={incident.pods_available} />
            <Row label="Pods desired" value={incident.pods_desired} />
            <Row label="Waiting reason" value={incident.waiting_reason} />
            <Row label="Last terminated reason" value={incident.last_terminated_reason} />
            {incident.restarts > 0 && <Row label="Restarts" value={incident.restarts} />}
          </dl>
          {dep && dep.pods_available === dep.pods_desired && !dep.waiting_reason && (
            <div className="mt-2 flex justify-between border-t border-dashed border-border pt-2 text-[11px] text-ink-faint">
              <span>Dependency: {dep.namespace}/{dep.name}</span>
              <span className="font-mono">{dep.pods_available}/{dep.pods_desired}</span>
            </div>
          )}
        </Card>

        {dep && (dep.pods_available !== dep.pods_desired || dep.waiting_reason) && (
          <Card className="border-critical bg-critical-soft">
            <CardTitle className="text-critical">
              <AlertTriangle size={14} /> Dependency Unhealthy
            </CardTitle>
            <dl className="m-0">
              <Row label="Name" value={`${dep.namespace}/${dep.name}`} />
              <Row label="Pods" value={`${dep.pods_available}/${dep.pods_desired}`} />
              <Row label="Waiting reason" value={dep.waiting_reason} />
            </dl>
          </Card>
        )}

        {hasInit && (
          <Card>
            <CardTitle><RefreshCw size={14} /> Init Container</CardTitle>
            <dl className="m-0">
              <Row label="Init waiting reason" value={incident.init_waiting_reason} />
              <Row label="Init last terminated reason" value={incident.init_last_terminated_reason} />
              {incident.init_restarts > 0 && <Row label="Init restarts" value={incident.init_restarts} />}
            </dl>
          </Card>
        )}
      </div>

      {td && (
        <Card>
          <CardTitle><History size={14} /> Recent Change</CardTitle>
          {td.env_changed && td.env_diff.map((d, i) => (
            <DiffBlock key={i} header={`${incident.service} · env.${d.key}`} oldValue={d.old_value} newValue={d.new_value} />
          ))}
          {td.image_changed && (
            <DiffBlock header={`${incident.service} · image`} oldValue={td.old_image} newValue={td.new_image} />
          )}
          <Row label="Changed at" value={td.changed_at} />
        </Card>
      )}

      {hasLogEvent && (
        <Card>
          <CardTitle><AlertTriangle size={14} /> Log &amp; Event</CardTitle>
          <dl className="m-0">
            <Row label="Log error" value={incident.log_error} />
            <Row label="Event reason" value={incident.event_reason} />
            <Row label="Event message" value={incident.event_message} />
            <Row label="Event object" value={incident.event_object} />
          </dl>
        </Card>
      )}
    </div>
  )
}
