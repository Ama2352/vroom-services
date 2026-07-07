import { Activity } from 'lucide-react'
import EvidenceCard from '../components/EvidenceCard.jsx'
import RootCauseCard from '../components/RootCauseCard.jsx'
import SuggestionSummary from '../components/SuggestionSummary.jsx'
import Timeline from '../components/Timeline.jsx'
import StatusBadge from '../components/StatusBadge.jsx'
import ErrorBanner from '../components/ErrorBanner.jsx'
import EmptyState from '../components/EmptyState.jsx'
import { SkeletonCard } from '../components/Skeleton.jsx'
import { useApiResource } from '../hooks/useApiResource.js'
import { api } from '../api.js'

const POLL_MS = 10000

export default function LivePage() {
  const { data: incident, loading, error, reload } = useApiResource(
    () => api.get('/incidents/latest').then(r => r.data.incident),
    [],
    POLL_MS,
  )

  if (error) return <ErrorBanner message={error} onRetry={reload} />
  if (loading) return <SkeletonCard lines={4} />
  if (incident === null) return <EmptyState message="No incidents yet." Icon={Activity} />

  return (
    <div>
      <h2>
        {incident.alert_name} — {incident.service}{' '}
        <StatusBadge status={incident.status} />
      </h2>
      <EvidenceCard incident={incident} />
      <RootCauseCard incident={incident} />
      <SuggestionSummary suggestion={incident.pending_suggestion} />
      <Timeline entries={incident.timeline} />
    </div>
  )
}
