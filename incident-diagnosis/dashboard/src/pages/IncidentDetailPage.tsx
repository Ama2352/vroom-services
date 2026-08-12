import { useParams } from 'react-router-dom'
import { EvidenceFirstIncident } from '../components/incident/EvidenceFirstIncident'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { SkeletonCard } from '../components/ui/Skeleton'
import { useApiResource } from '../hooks/useApiResource'
import { api } from '../lib/api'
import type { V2Incident } from '../types/incident'

export function IncidentDetailPage() {
  const { id } = useParams()
  const { data, loading, error, reload } = useApiResource<V2Incident>(
    () => api.get(`/incidents/${id}`).then(response => response.data.incident), [id],
  )
  if (error) return <ErrorBanner message={error} onRetry={reload} />
  if (loading || !data) return <SkeletonCard lines={4} />
  return <EvidenceFirstIncident incident={data} />
}
