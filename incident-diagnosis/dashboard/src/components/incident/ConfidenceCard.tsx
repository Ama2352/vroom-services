import type { DiagnosisConfidence } from '../../types/incident'
import { Card, CardTitle } from '../ui/Card'

export function ConfidenceCard({ confidence }: { confidence?: DiagnosisConfidence }) {
  const value = confidence ?? { level: 'unknown' as const, reasons: [], missing_evidence: ['confidence evidence unavailable'] }
  return <Card>
    <div className="flex items-center justify-between"><CardTitle>Diagnosis confidence</CardTitle><span className="rounded-full bg-info-soft px-2 py-1 text-xs font-bold uppercase text-info">{value.level}</span></div>
    {value.reasons.length > 0 && <ul className="mt-2 list-disc pl-5 text-sm text-ink">{value.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>}
    {value.missing_evidence.length > 0 && <p className="mt-2 text-xs text-muted">Missing: {value.missing_evidence.join('; ')}</p>}
  </Card>
}
