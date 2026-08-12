import { Activity } from 'lucide-react'
import { EvidenceFirstIncident } from '../components/incident/EvidenceFirstIncident'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { EmptyState } from '../components/ui/EmptyState'
import { SkeletonCard } from '../components/ui/Skeleton'
import { useApiResource } from '../hooks/useApiResource'
import { api } from '../lib/api'
import type { V2Incident } from '../types/incident'

export function LivePage() {
  const { data: incident, loading, error, reload } = useApiResource<V2Incident | null>(
    () => api.get('/incidents/latest').then(response => response.data.incident), [], 10000,
  )
  if (error) return <ErrorBanner message={error} onRetry={reload} />
  if (loading) return <SkeletonCard lines={4} />
  if (!incident) return <EmptyState message="No incidents yet." Icon={Activity} />
  return <EvidenceFirstIncident incident={incident} />
}
