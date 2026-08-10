import { AlertTriangle, CheckCircle2, CircleHelp } from 'lucide-react'
import type { IncidentPresentation } from '../../types/incident'
import { Card } from '../ui/Card'

const verdictCopy = {
  cause_confirmed: 'Cause confirmed',
  review_required: 'Review required',
  evaluation_unavailable: 'Evaluation unavailable',
} as const

function VerdictIcon({ verdict }: { verdict: IncidentPresentation['verdict'] }) {
  if (verdict === 'cause_confirmed') return <CheckCircle2 size={15} aria-hidden="true" />
  if (verdict === 'review_required') return <AlertTriangle size={15} aria-hidden="true" />
  return <CircleHelp size={15} aria-hidden="true" />
}

export function DiagnosisSummary({ presentation }: { presentation: IncidentPresentation }) {
  const confirmed = presentation.verdict === 'cause_confirmed'
  return (
    <Card className={confirmed ? 'border-healthy bg-healthy-soft' : 'border-root-cause bg-root-cause-soft'}>
      <div className="flex items-center gap-2 text-[10.5px] font-bold uppercase tracking-wide text-ink-soft">
        <VerdictIcon verdict={presentation.verdict} />
        Diagnosis
        <span className="rounded-full bg-surface px-2 py-0.5 text-[10px] font-bold normal-case tracking-normal text-ink">
          {verdictCopy[presentation.verdict]}
        </span>
      </div>
      <h3 className="mt-2 text-[17px] font-bold leading-snug text-ink">{presentation.headline}</h3>
      {presentation.summary && <p className="mt-2 text-sm text-ink-soft">{presentation.summary}</p>}
      {presentation.hypothesis && (
        <div className="mt-3 rounded-md border border-border bg-surface px-3 py-2">
          <div className="text-[10px] font-bold uppercase tracking-wide text-ink-faint">Most plausible explanation — unconfirmed</div>
          <div className="mt-1 text-xs text-ink">{presentation.hypothesis}</div>
        </div>
      )}
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        <div className="rounded-md border border-border bg-surface px-3 py-2">
          <div className="text-[10px] font-bold uppercase tracking-wide text-ink-faint">Confirmed failure</div>
          <div className="mt-1 text-xs text-ink">{presentation.confirmed_failure}</div>
        </div>
        <div className="rounded-md border border-border bg-surface px-3 py-2">
          <div className="text-[10px] font-bold uppercase tracking-wide text-ink-faint">
            {presentation.causal_basis ? 'Causal basis' : 'Evidence gap'}
          </div>
          <div className={`mt-1 text-xs ${presentation.causal_basis ? 'text-healthy' : 'text-root-cause-label'}`}>
            {presentation.causal_basis || presentation.evidence_gap || 'No additional causal explanation was recorded.'}
          </div>
        </div>
      </div>
      {(presentation.mechanism_status || presentation.attribution_status) && (
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          <div className="rounded-md border border-border bg-surface px-3 py-2">
            <div className="text-[10px] font-bold uppercase tracking-wide text-ink-faint">Failure mechanism</div>
            <div className="mt-1 text-xs font-semibold text-ink">{presentation.mechanism_status === 'confirmed' ? 'Confirmed by runtime evidence' : 'Not established'}</div>
          </div>
          <div className="rounded-md border border-border bg-surface px-3 py-2">
            <div className="text-[10px] font-bold uppercase tracking-wide text-ink-faint">Change attribution</div>
            <div className="mt-1 text-xs font-semibold text-ink">{presentation.attribution_status === 'confirmed' ? 'Confirmed' : presentation.attribution_status === 'unproven' ? 'Not proven' : 'Unavailable'}</div>
          </div>
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-ink-soft">
        <span>Evidence confidence: <strong className="text-ink">{presentation.evidence_confidence}</strong></span>
        <span>Response source: <strong className="text-ink">{presentation.answer_source}</strong></span>
      </div>
    </Card>
  )
}
