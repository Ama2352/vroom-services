import { Activity } from 'lucide-react'
import { DiagnosisSummary } from '../components/incident/DiagnosisSummary'
import { SupportingEvidence } from '../components/incident/SupportingEvidence'
import { RecommendedResponse } from '../components/incident/RecommendedResponse'
import { EvidenceSections } from '../components/incident/EvidenceSections'
import { IncidentTimeline } from '../components/incident/IncidentTimeline'
import { AgentAudit } from '../components/incident/AgentAudit'
import StatusBadge from '../components/ui/Badge'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { EmptyState } from '../components/ui/EmptyState'
import { SkeletonCard } from '../components/ui/Skeleton'
import { useApiResource } from '../hooks/useApiResource'
import { api } from '../lib/api'
import type { Incident, IncidentOccurrence, IncidentPresentation, OccurrenceEvidence } from '../types/incident'

const POLL_MS = 10000

function occurrenceFor(incident: Incident): IncidentOccurrence {
  const occurrence = incident.occurrences?.[incident.selected_occurrence ?? (incident.occurrences.length - 1)]
  if (occurrence) return occurrence
  const presentation: IncidentPresentation = incident.presentation || {
    verdict: incident.low_confidence ? 'review_required' : 'evaluation_unavailable',
    headline: incident.root_cause,
    summary: 'The stored incident does not include the redesigned presentation.',
    confirmed_failure: incident.log_error || incident.root_cause,
    causal_basis: null,
    evidence_gap: 'Presentation data unavailable',
    evidence_confidence: incident.diagnosis_confidence?.level || 'unknown',
    answer_source: 'safe_fallback',
    supporting_evidence: [],
    recommended_response: { mode: 'investigation', summary: incident.dev_action, command: incident.kubectl_hint },
    incident_events: [],
  }
  return { index: 0, fired_at: incident.timestamp, presentation, evidence: incident as unknown as OccurrenceEvidence, agent_steps: incident.timeline.filter(entry => entry.type === 'step') }
}

export function LivePage() {
  const { data: incident, loading, error, reload } = useApiResource<Incident | null>(
    () => api.get('/incidents/latest').then(r => r.data.incident),
    [],
    POLL_MS,
  )

  if (error) return <ErrorBanner message={error} onRetry={reload} />
  if (loading) return <SkeletonCard lines={4} />
  if (!incident) return <EmptyState message="No incidents yet." Icon={Activity} />

  const occurrence = occurrenceFor(incident)
  return (
    <div className="min-w-0 space-y-4">
        <h2 className="flex flex-wrap items-center gap-2 text-base font-bold text-ink">
          {incident.alert_name} — {incident.service}
          <StatusBadge status={incident.status} />
        </h2>
        <DiagnosisSummary presentation={occurrence.presentation} />
        <RecommendedResponse response={occurrence.presentation.recommended_response} />
        <SupportingEvidence presentation={occurrence.presentation} />
        <EvidenceSections evidence={occurrence.evidence} />
        <IncidentTimeline occurrence={occurrence} />
        <AgentAudit occurrence={occurrence} />
    </div>
  )
}
