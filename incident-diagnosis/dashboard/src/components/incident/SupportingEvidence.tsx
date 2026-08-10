import { ExternalLink } from 'lucide-react'
import type { IncidentPresentation, PresentationEvidenceState } from '../../types/incident'
import { Card } from '../ui/Card'

const stateLabels: Record<PresentationEvidenceState, string> = {
  confirmed: 'Confirmed', context: 'Context only', missing: 'Missing', conflicting: 'Conflicting',
}

export function SupportingEvidence({ presentation }: { presentation: IncidentPresentation }) {
  const hasContextualState = presentation.supporting_evidence.some(item => item.state !== 'confirmed')
  return (
    <Card>
      <div className="mb-2 text-[10.5px] font-semibold uppercase tracking-wide text-accent">Supporting evidence</div>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {presentation.supporting_evidence.map(item => (
          <div key={item.id} className="rounded-md border border-border bg-canvas px-3 py-2">
            <div className={`text-[10px] font-bold uppercase tracking-wide ${item.state === 'confirmed' ? 'text-healthy' : 'text-root-cause-label'}`}>
              {stateLabels[item.state]}
            </div>
            <div className="mt-1 text-xs font-semibold text-ink">{item.label}</div>
            <div className="mt-1 break-words text-[11px] text-ink-soft">{item.value}</div>
            {item.href && <a className="mt-2 inline-flex items-center gap-1 text-[11px] text-accent hover:text-accent-strong" href={item.href} target="_blank" rel="noreferrer">Open source <ExternalLink size={11} aria-hidden="true" /></a>}
          </div>
        ))}
      </div>
      {hasContextualState && <p className="mt-2 text-[11px] text-ink-faint"><strong className="text-ink-soft">Context only</strong> means relevant evidence without a demonstrated causal connection.</p>}
      {presentation.supporting_evidence.length === 0 && <p className="m-0 text-xs text-ink-faint">No supporting evidence was recorded for this occurrence.</p>}
    </Card>
  )
}
