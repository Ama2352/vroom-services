import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { PhaseItem } from '../../utils/groupTimeline'
import { formatDuration } from '../../lib/format'

const STATUS_DOT: Record<PhaseItem['status'], string> = {
  neutral: 'bg-border-strong',
  ok: 'bg-healthy',
  warn: 'bg-root-cause',
  error: 'bg-critical',
}

export function TimelinePhaseGroup({ phase }: { phase: PhaseItem }) {
  const [expanded, setExpanded] = useState(false)
  const Icon = phase.Icon
  return (
    <div className="relative">
      <span className="absolute -left-6 top-0.5 flex h-[22px] w-[22px] items-center justify-center rounded-full border-2 border-border-strong bg-white text-ink-soft">
        <Icon size={12} />
      </span>
      <button onClick={() => setExpanded(!expanded)} className="flex w-full items-center gap-2 text-left text-ink">
        <span className={`h-2 w-2 shrink-0 rounded-full ${STATUS_DOT[phase.status]}`} />
        <span className="text-sm font-semibold">{phase.name}</span>
        <span className="rounded-full bg-accent-soft px-2 py-px font-mono text-[11px] text-accent">
          {formatDuration(phase.durationMs)}
        </span>
        {expanded
          ? <ChevronDown size={14} className="ml-auto text-ink-faint" />
          : <ChevronRight size={14} className="ml-auto text-ink-faint" />}
      </button>
      {expanded && (
        <div className="ml-4 mt-2 flex flex-col gap-2 border-l border-border pl-4">
          {phase.steps.map((s, i) => {
            const metaEntries = Object.entries(s.metadata || {})
            return (
              <div key={i} className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-ink-soft">{s.name}</span>
                <span className="rounded-full bg-accent-soft px-2 py-px font-mono text-[11px] text-accent">
                  {formatDuration(s.duration_ms)}
                </span>
                {metaEntries.map(([k, v]) => (
                  <span key={k} className="rounded-md border border-border bg-canvas px-2 py-0.5 font-mono text-[11px] text-ink-soft">
                    {k}: {String(v)}
                  </span>
                ))}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
