import type { DiagnosisConfidence } from '../../types/incident'

export function ConfidenceCard({ confidence }: { confidence?: DiagnosisConfidence }) {
  const value = confidence ?? { level: 'unknown' as const, reasons: [], missing_evidence: ['confidence evidence unavailable'] }
  return <section className="rounded-lg border border-line bg-white p-4 shadow-sm">
    <div className="flex items-center justify-between"><h3 className="font-semibold text-ink">Diagnosis confidence</h3><span className="rounded-full bg-slate-100 px-2 py-1 text-xs font-bold uppercase text-slate-700">{value.level}</span></div>
    {value.reasons.length > 0 && <ul className="mt-2 list-disc pl-5 text-sm text-ink">{value.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}
    {value.missing_evidence.length > 0 && <p className="mt-2 text-xs text-muted">Missing: {value.missing_evidence.join('; ')}</p>}
  </section>
}
