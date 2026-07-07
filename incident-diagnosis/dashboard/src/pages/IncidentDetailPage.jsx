import { useState } from 'react'
import { useParams } from 'react-router-dom'
import EvidenceCard from '../components/EvidenceCard.jsx'
import RootCauseCard from '../components/RootCauseCard.jsx'
import SuggestionSummary from '../components/SuggestionSummary.jsx'
import Timeline from '../components/Timeline.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import ErrorBanner from '../components/ErrorBanner.jsx'
import { SkeletonCard } from '../components/Skeleton.jsx'
import { useApiResource } from '../hooks/useApiResource.js'
import { api } from '../api.js'
import { getActor } from '../actor.js'

export default function IncidentDetailPage() {
  const { id } = useParams()
  const { data: incident, loading, error, reload } = useApiResource(
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
  if (loading) return <SkeletonCard lines={4} />

  return (
    <div className="detail-layout">
      <div className="content-column">
        <h2>
          {incident.alert_name} — {incident.service}{' '}
          <StatusBadge status={incident.status} />
        </h2>
        <EvidenceCard incident={incident} />
        <RootCauseCard incident={incident} />
        <SuggestionSummary suggestion={incident.pending_suggestion} />
        {incident.status === 'open' && (
          <button className="button" onClick={resolve} disabled={resolving}>
            {resolving ? 'Resolving…' : 'Resolve Incident'}
          </button>
        )}
      </div>
      <Timeline entries={incident.timeline} />
    </div>
  )
}
