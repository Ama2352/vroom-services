import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { EvidenceGrid } from '../components/incident/EvidenceGrid'
import { RootCauseCard } from '../components/incident/RootCauseCard'
import { ImmediateFixCard } from '../components/incident/ImmediateFixCard'
import { KnowledgeSuggestionCard } from '../components/incident/KnowledgeSuggestionCard'
import { Timeline } from '../components/incident/Timeline'
import StatusBadge from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { SkeletonCard } from '../components/ui/Skeleton'
import { useApiResource } from '../hooks/useApiResource'
import { api } from '../lib/api'
import { getActor } from '../lib/actor'
import type { Incident } from '../types/incident'

export function IncidentDetailPage() {
  const { id } = useParams()
  const { data: incident, loading, error, reload } = useApiResource<Incident>(
    () => api.get(`/incidents/${id}`).then(r => r.data.incident),
    [id],
  )
  const [resolving, setResolving] = useState(false)

  function resolve() {
    setResolving(true)
    api.post(`/incidents/${id}/resolve`, { actor: getActor() })
      .then(reload)
      .finally(() => setResolving(false))
  }

  if (error) return <ErrorBanner message={error} onRetry={reload} />
  if (loading || !incident) return <SkeletonCard lines={4} />

  return (
    <div className="flex items-start gap-6 max-[1100px]:flex-col">
      <div className="min-w-0 flex-1 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-base font-bold text-ink">{incident.alert_name} — {incident.service}</h2>
          <StatusBadge status={incident.status} />
          {incident.status === 'open' && (
            <Button className="ml-auto" onClick={resolve} disabled={resolving}>
              {resolving ? 'Resolving…' : 'Resolve Incident'}
            </Button>
          )}
        </div>
        <RootCauseCard incident={incident} />
        <EvidenceGrid incident={incident} />
        <ImmediateFixCard incident={incident} />
        {incident.pending_suggestion && <KnowledgeSuggestionCard suggestion={incident.pending_suggestion} />}
      </div>
      <Timeline entries={incident.timeline} mode="full" />
    </div>
  )
}
