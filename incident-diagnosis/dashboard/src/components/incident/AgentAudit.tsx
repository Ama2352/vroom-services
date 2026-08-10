import { ChevronDown, ChevronRight, ClipboardCheck } from 'lucide-react'
import { useState } from 'react'
import type { IncidentOccurrence } from '../../types/incident'
import { groupAgentAudit } from '../../utils/groupTimeline'
import { formatDuration, formatTimestamp } from '../../lib/format'
import { Card } from '../ui/Card'

const VERDICT_STYLE = {
  passed: 'text-healthy',
  rejected: 'text-critical',
  degraded: 'text-root-cause-label',
  informational: 'text-ink-faint',
  failed: 'text-critical',
} as const

export function AgentAudit({ occurrence }: { occurrence: IncidentOccurrence }) {
  const [open, setOpen] = useState(false)
  const phases = groupAgentAudit(occurrence.agent_steps || [])
  return (
    <Card className="border-border bg-surface/70">
      <button onClick={() => setOpen(value => !value)} className="flex w-full items-center gap-2 text-left">
        <ClipboardCheck size={14} className="text-accent" />
        <span className="text-[10.5px] font-semibold uppercase tracking-wide text-accent">Agent audit</span>
        <span className="text-xs text-ink-faint">{phases.length ? `${phases.length} phases` : 'No audit details'}</span>
        {open ? <ChevronDown size={15} className="ml-auto text-ink-faint" /> : <ChevronRight size={15} className="ml-auto text-ink-faint" />}
      </button>
      {open && (
        <div className="mt-3 border-t border-border pt-3">
          {phases.length === 0 ? <p className="m-0 text-xs text-ink-faint">Agent audit data is unavailable for this occurrence.</p> : (
            <ol className="m-0 grid gap-2 pl-0 [list-style:none]">
              {phases.map(phase => (
                <li key={phase.name} className="rounded-md border border-border bg-canvas p-2.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`text-xs font-semibold ${VERDICT_STYLE[phase.verdict]}`}>{phase.name}</span>
                    <span className="rounded-full bg-accent-soft px-2 py-px font-mono text-[10px] text-accent">{formatDuration(phase.durationMs)}</span>
                    <span className="ml-auto text-[10px] uppercase tracking-wide text-ink-faint">{phase.verdict}</span>
                  </div>
                  <div className="mt-2 grid gap-1.5">
                    {phase.steps.map((step, index) => (
                      <div key={`${step.name}-${index}`} className="flex flex-wrap items-center gap-2 text-[11px]">
                        <span className="text-ink-soft">{step.name}</span>
                        <span className="font-mono text-ink-faint">{formatDuration(step.duration_ms || 0)}</span>
                        {Object.entries(step.metadata || {}).map(([key, value]) => <span key={key} className="rounded border border-border px-1.5 py-0.5 font-mono text-ink-faint">{key}: {String(value)}</span>)}
                        <span className="ml-auto text-[10px] text-ink-faint">{formatTimestamp(step.timestamp)}</span>
                      </div>
                    ))}
                  </div>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </Card>
  )
}
