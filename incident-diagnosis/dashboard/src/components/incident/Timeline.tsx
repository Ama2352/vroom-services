import type { TimelineEntry, TimelineFiredEntry, TimelineResolvedEntry } from '../../types/incident'
import { groupTimeline, splitOccurrences } from '../../utils/groupTimeline'
import { TimelinePhaseGroup } from './TimelinePhaseGroup'
import { formatTimestamp } from '../../lib/format'

function FiredCard({ entry }: { entry: TimelineFiredEntry }) {
  const reason = entry.evidence_snapshot?.waiting_reason
  return (
    <div>
      <div className="flex items-center gap-2">
        <span className="h-2 w-2 shrink-0 rounded-full bg-critical" />
        <span className="text-sm font-semibold text-ink">Fired</span>
        <span className="ml-auto text-xs text-ink-faint">{formatTimestamp(entry.timestamp)}</span>
      </div>
      {reason && (
        <span className="mt-1 ml-4 inline-block rounded-md border border-border bg-canvas px-2 py-0.5 font-mono text-[11px] text-ink-soft">
          waiting_reason: {reason}
        </span>
      )}
    </div>
  )
}

function ResolvedCard({ entry }: { entry: TimelineResolvedEntry }) {
  return (
    <div>
      <div className="flex items-center gap-2">
        <span className="h-2 w-2 shrink-0 rounded-full bg-healthy" />
        <span className="text-sm font-semibold text-ink">Resolved</span>
        <span className="ml-auto text-xs text-ink-faint">{formatTimestamp(entry.timestamp)}</span>
      </div>
      {entry.actor && (
        <span className="mt-1 ml-4 inline-block rounded-md border border-border bg-canvas px-2 py-0.5 font-mono text-[11px] text-ink-soft">
          by {entry.actor}
        </span>
      )}
    </div>
  )
}

function OccurrenceGroup({ entries, label }: { entries: TimelineEntry[]; label?: string | null }) {
  const items = groupTimeline(entries)
  return (
    <>
      {label && (
        <div className="my-3 flex items-center gap-2 text-xs text-ink-faint">
          {label}
          <span className="flex-1 border-t border-border" />
        </div>
      )}
      {items.map((item, i) => {
        if (item.kind === 'fired') return <FiredCard key={i} entry={item.entry} />
        if (item.kind === 'resolved') return <ResolvedCard key={i} entry={item.entry} />
        return <TimelinePhaseGroup key={i} phase={item} />
      })}
    </>
  )
}

export function Timeline({ entries, mode = 'full' }: { entries: TimelineEntry[]; mode?: 'latest' | 'full' }) {
  const occurrences = splitOccurrences(entries)
  const total = occurrences.length
  const shown = mode === 'latest' ? occurrences.slice(-1) : occurrences
  return (
    <div className="w-[280px] shrink-0 self-start rounded-[10px] border border-border bg-surface p-3 sticky top-6 max-[1100px]:static max-[1100px]:w-full">
      <div className="mb-3 flex items-center justify-between text-[10.5px] font-semibold uppercase tracking-wide text-ink-soft">
        Timeline
        {mode === 'latest' && total > 1 && (
          <span className="text-[11px] font-normal normal-case text-ink-faint">latest of {total}</span>
        )}
      </div>
      <div className="flex flex-col gap-3">
        {shown.map((occ, i) => (
          <OccurrenceGroup
            key={i}
            entries={occ}
            label={mode === 'full' && total > 1 ? `Occurrence ${i + 1} of ${total}` : null}
          />
        ))}
      </div>
    </div>
  )
}
