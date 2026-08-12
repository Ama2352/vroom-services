import { ExternalLink } from 'lucide-react'
import type { V2Incident, RawEvidenceCard } from '../../types/incident'

const stateDetail = {
  confirmed: 'The source explicitly reports this observation.',
  context: 'Relevant context; it does not establish a cause.',
  not_found: 'This source was checked but no relevant observation was found.',
}

function EvidenceCard({ card }: { card: RawEvidenceCard }) {
  return <article className="rounded-xl border border-border bg-slate-950/35 p-3">
    <span title={stateDetail[card.state]} className="inline-flex cursor-help rounded-full bg-accent-soft px-2 py-1 text-[10px] font-bold uppercase tracking-wide text-accent">{card.state.replace('_', ' ')}</span>
    <h3 className="mt-2 text-sm font-semibold text-ink">{card.title}</h3>
    <dl className="mt-2 space-y-1.5 text-sm">
      {card.rows.map(row => <div key={row.field} className="grid grid-cols-[7.5rem_1fr] gap-2 border-t border-border/70 pt-1.5">
        <dt className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{row.field}</dt>
        <dd className="break-words text-ink-soft">{row.value === null ? 'No data' : `${row.value}${row.unit ? ` ${row.unit}` : ''}`}</dd>
      </div>)}
    </dl>
    {card.href && <a className="mt-3 inline-flex items-center gap-1 text-xs font-semibold text-accent hover:text-accent-strong" href={card.href} target="_blank" rel="noreferrer">Open source <ExternalLink size={12} /></a>}
  </article>
}

export function EvidenceFirstIncident({ incident }: { incident: V2Incident }) {
  const { diagnosis, raw_evidence, retrieval } = incident
  const cards = Object.entries(raw_evidence)
  const metrics = raw_evidence.metrics?.rows ?? []
  return <div className="space-y-4">
    <section className="rounded-2xl border border-root-cause/70 bg-root-cause-soft p-5">
      <div className="text-[11px] font-bold uppercase tracking-widest text-root-cause-label">Diagnosis · {retrieval.mode === 'exact' ? 'cause confirmed' : 'review required'}</div>
      <h1 className="mt-2 text-xl font-bold text-ink">{diagnosis.incident_summary}</h1>
      {diagnosis.diagnosis_cause && <div className="mt-4 rounded-lg border border-border bg-surface/80 p-3"><div className="text-[10px] font-bold uppercase tracking-wide text-ink-faint">Diagnosis cause</div><div className="mt-1 font-semibold text-ink">{diagnosis.diagnosis_cause}</div></div>}
      {!diagnosis.diagnosis_cause && diagnosis.hypothesis && <div className="mt-4 rounded-lg border border-info/50 bg-info-soft p-3"><div className="text-[10px] font-bold uppercase tracking-wide text-info">Hypothesis — unconfirmed</div><div className="mt-1 font-semibold text-ink">{diagnosis.hypothesis}</div></div>}
    </section>
    <div className="grid gap-4 xl:grid-cols-[1.25fr_.75fr]">
      <section className="space-y-4"><div className="rounded-2xl border border-border bg-surface p-4"><h2 className="text-xs font-bold uppercase tracking-widest text-accent">Raw current evidence</h2><div className="mt-3 grid gap-3 md:grid-cols-2">{cards.filter(([key]) => key !== 'metrics').map(([key, card]) => <EvidenceCard key={key} card={card} />)}</div></div>
        <section className="rounded-2xl border border-healthy/60 bg-healthy-soft p-4"><h2 className="text-xs font-bold uppercase tracking-widest text-accent">Recommended next action</h2><p className="mt-2 font-semibold text-ink">{diagnosis.recommended_action.summary}</p></section>
      </section>
      <aside className="space-y-4"><section className="rounded-2xl border border-border bg-surface p-4"><h2 className="text-xs font-bold uppercase tracking-widest text-accent">Operational context</h2><div className="mt-3 grid grid-cols-2 gap-2">{metrics.map(metric => <div key={metric.field} className="rounded-lg border border-border bg-slate-950/35 p-3"><div className="text-sm font-bold text-ink">{metric.value === null ? '—' : metric.value} {metric.unit}</div><div className="mt-1 text-xs text-ink-soft">{metric.field}</div></div>)}</div></section>
      {retrieval.mode !== 'exact' && <section className="rounded-2xl border border-border bg-surface p-4"><h2 className="text-xs font-bold uppercase tracking-widest text-accent">Nearest approved examples</h2><div className="mt-3 space-y-2">{retrieval.advisory_examples.map((example, index) => <article key={example.example_id} className="rounded-lg border border-border bg-slate-950/35 p-3"><div className="text-sm font-semibold text-ink">{index + 1}. {example.knowledge_key.replaceAll('_', ' ')}</div><p className="mt-1 line-clamp-3 text-xs text-ink-soft">{example.evidence_template}</p></article>)}</div></section>}</aside>
    </div>
  </div>
}
