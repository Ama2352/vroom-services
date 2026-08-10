import { AlertTriangle, CheckCircle2, GitBranch, Server, Workflow } from 'lucide-react'
import type { IncidentEvent, IncidentOccurrence } from '../../types/incident'
import { Card, CardTitle } from '../ui/Card'

const ICONS = {
  metric: Workflow,
  log: AlertTriangle,
  trace: Workflow,
  kubernetes: Server,
  change: GitBranch,
  dependency: Server,
} as const

function EventRow({ event }: { event: IncidentEvent }) {
  const Icon = ICONS[event.id.split(':')[0] as keyof typeof ICONS] || CheckCircle2
  const tone = event.state === 'confirmed' ? 'border-healthy bg-healthy-soft' : event.state === 'conflicting' ? 'border-critical bg-critical-soft' : 'border-border bg-canvas'
  return (
    <li className={`rounded-lg border p-3 ${tone}`}>
      <div className="flex items-start gap-3">
        <span className="mt-0.5 rounded-md bg-surface p-1.5 text-accent"><Icon size={14} /></span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-ink">{event.title}</span>
            {event.occurred_at && <span className="text-[11px] text-ink-faint">{new Date(event.occurred_at).toLocaleString()}</span>}
          </div>
          <p className="mt-1 text-xs leading-5 text-ink-soft">{event.summary}</p>
        </div>
        <span className="shrink-0 rounded-full border border-border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{event.state}</span>
      </div>
    </li>
  )
}

export function IncidentTimeline({ occurrence }: { occurrence: IncidentOccurrence }) {
  const events = occurrence.presentation.incident_events || []
  return (
    <Card>
      <CardTitle><Workflow size={14} /> Incident chronology</CardTitle>
      {events.length === 0 ? (
        <p className="m-0 text-xs text-ink-faint">No causal event sequence was recorded for this occurrence.</p>
      ) : (
        <ol className="m-0 grid gap-2 pl-0 [list-style:none]">{events.map(event => <EventRow key={event.id} event={event} />)}</ol>
      )}
    </Card>
  )
}
