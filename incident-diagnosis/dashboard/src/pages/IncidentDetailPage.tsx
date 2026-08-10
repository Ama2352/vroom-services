import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { DiagnosisSummary } from '../components/incident/DiagnosisSummary'
import { SupportingEvidence } from '../components/incident/SupportingEvidence'
import { RecommendedResponse } from '../components/incident/RecommendedResponse'
import { EvidenceSections } from '../components/incident/EvidenceSections'
import { AgentAudit } from '../components/incident/AgentAudit'
import StatusBadge from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { SkeletonCard } from '../components/ui/Skeleton'
import { useApiResource } from '../hooks/useApiResource'
import { api } from '../lib/api'
import { getActor } from '../lib/actor'
import type { Incident, IncidentOccurrence, IncidentPresentation, OccurrenceEvidence } from '../types/incident'

function fallbackPresentation(incident: Incident): IncidentPresentation {
  return {
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
}

function latestOccurrence(incident: Incident): IncidentOccurrence {
  const fromApi = incident.occurrences?.[incident.selected_occurrence ?? (incident.occurrences.length - 1)]
  if (fromApi) return fromApi
  return {
    index: 0,
    fired_at: incident.timestamp,
    presentation: incident.presentation || fallbackPresentation(incident),
    evidence: incident as unknown as OccurrenceEvidence,
    agent_steps: incident.timeline.filter(entry => entry.type === 'step'),
  }
}

export function IncidentDetailPage() {
  const { id } = useParams()
  const { data: incident, loading, error, reload } = useApiResource<Incident>(
    () => api.get(`/incidents/${id}`).then(r => r.data.incident),
    [id],
  )
  const [resolving, setResolving] = useState(false)
  const [occurrenceIndex, setOccurrenceIndex] = useState<number | null>(null)

  function resolve() {
    setResolving(true)
    api.post(`/incidents/${id}/resolve`, { actor: getActor() })
      .then(reload)
      .finally(() => setResolving(false))
  }

  if (error) return <ErrorBanner message={error} onRetry={reload} />
  if (loading || !incident) return <SkeletonCard lines={4} />

  const occurrences = incident.occurrences || []
  const selected = occurrenceIndex === null ? latestOccurrence(incident) : occurrences[occurrenceIndex] || latestOccurrence(incident)

  return (
    <div className="min-w-0 space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-bold text-ink">{incident.alert_name} — {incident.service}</h2>
        <StatusBadge status={incident.status} />
        {incident.status === 'open' && (
          <Button className="ml-auto" onClick={resolve} disabled={resolving}>
            {resolving ? 'Resolving…' : 'Resolve Incident'}
          </Button>
        )}
      </div>
      {occurrences.length > 1 && (
        <label className="flex items-center gap-2 text-xs text-ink-soft">
          Occurrence
          <select className="rounded-md border border-border bg-surface px-2 py-1 text-xs text-ink" value={occurrenceIndex ?? occurrences.length - 1} onChange={event => setOccurrenceIndex(Number(event.target.value))}>
            {occurrences.map(occurrence => <option key={occurrence.index} value={occurrence.index}>#{occurrence.index + 1} · {new Date(occurrence.fired_at * 1000).toLocaleString()}</option>)}
          </select>
        </label>
      )}
      <DiagnosisSummary presentation={selected.presentation} />
      <RecommendedResponse response={selected.presentation.recommended_response} />
      <SupportingEvidence presentation={selected.presentation} />
      <EvidenceSections evidence={selected.evidence} />
      <AgentAudit occurrence={selected} />
    </div>
  )
}
