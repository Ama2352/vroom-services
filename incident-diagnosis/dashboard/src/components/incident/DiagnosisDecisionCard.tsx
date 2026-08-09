export interface DiagnosisDecision {
  status: string
  published_generated_answer: boolean
}

export interface CausalChainSummary {
  incident_kind: string
  trigger_ids: string[]
  primary_ids: string[]
  causal_context_ids: string[]
  contradiction_ids: string[]
}

function EvidenceList({ label, ids }: { label: string; ids: string[] }) {
  if (!ids.length) return null
  return (
    <div>
      <div className="mb-1 text-[10px] font-bold uppercase tracking-wide text-ink-faint">{label}</div>
      <div className="flex flex-wrap gap-1.5">
        {ids.map(id => <code key={id} className="rounded-md border border-border bg-canvas px-1.5 py-0.5 text-[11px] text-ink-soft">{id}</code>)}
      </div>
    </div>
  )
}

export function DiagnosisDecisionCard({ decision, chain }: {
  decision?: DiagnosisDecision | null
  chain?: CausalChainSummary | null
}) {
  if (!decision && !chain) return null
  const reviewRequired = !decision?.published_generated_answer
  return (
    <section className={`rounded-[10px] border px-4 py-3.5 ${reviewRequired ? 'border-root-cause bg-root-cause-soft' : 'border-border bg-surface'}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10.5px] font-bold uppercase tracking-wide text-ink-soft">Diagnosis decision</span>
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${reviewRequired ? 'bg-surface text-root-cause-label' : 'bg-healthy-soft text-healthy'}`}>
          {reviewRequired ? 'Review required' : 'Accepted'}
        </span>
        {chain?.incident_kind && <span className="text-xs text-ink-faint">{chain.incident_kind} evidence path</span>}
      </div>
      {reviewRequired && <p className="mt-2 text-sm text-ink-soft">Generated remediation was withheld.</p>}
      {chain && (
        <div className="mt-3 grid gap-3 border-t border-border pt-3">
          <EvidenceList label="Trigger" ids={chain.trigger_ids} />
          <EvidenceList label="Primary evidence" ids={chain.primary_ids} />
          <EvidenceList label="Causal context" ids={chain.causal_context_ids} />
          <EvidenceList label="Contradictions" ids={chain.contradiction_ids} />
        </div>
      )}
    </section>
  )
}
