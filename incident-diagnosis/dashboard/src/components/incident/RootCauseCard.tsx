import type { Incident } from '../../types/incident'

export function RootCauseCard({ incident }: { incident: Incident }) {
  return (
    <div className="rounded-[10px] border-[1.5px] border-root-cause bg-root-cause-soft px-4 py-3.5 shadow-[0_1px_3px_rgba(217,119,6,0.14)]">
      <div className="mb-1.5 flex items-center gap-2 text-[10.5px] font-bold uppercase tracking-wide text-root-cause-label">
        Root Cause
        {incident.low_confidence && (
          <span className="rounded-full bg-surface px-1.5 py-0.5 text-[9.5px] font-bold normal-case tracking-normal text-root-cause-label">
            Low confidence
          </span>
        )}
      </div>
      <div className="text-[17px] font-bold leading-snug text-ink">{incident.root_cause}</div>
    </div>
  )
}
