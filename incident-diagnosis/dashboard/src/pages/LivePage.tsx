import { Activity } from 'lucide-react'
import { EvidenceGrid } from '../components/incident/EvidenceGrid'
import { RootCauseCard } from '../components/incident/RootCauseCard'
import { ImmediateFixCard } from '../components/incident/ImmediateFixCard'
import { KnowledgeSuggestionCard } from '../components/incident/KnowledgeSuggestionCard'
import { Timeline } from '../components/incident/Timeline'
import StatusBadge from '../components/ui/Badge'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { EmptyState } from '../components/ui/EmptyState'
import { SkeletonCard } from '../components/ui/Skeleton'
import { useApiResource } from '../hooks/useApiResource'
import { api } from '../lib/api'
import type { Incident } from '../types/incident'

const POLL_MS = 10000

export function LivePage() {
  const { data: incident, loading, error, reload } = useApiResource<Incident | null>(
    () => api.get('/incidents/latest').then(r => r.data.incident),
    [],
    POLL_MS,
  )

  if (error) return <ErrorBanner message={error} onRetry={reload} />
  if (loading) return <SkeletonCard lines={4} />
  if (!incident) return <EmptyState message="No incidents yet." Icon={Activity} />

  return (
    <div className="flex items-start gap-6 max-[1100px]:flex-col">
      <div className="min-w-0 flex-1 space-y-3">
        <h2 className="flex flex-wrap items-center gap-2 text-base font-bold text-ink">
          {incident.alert_name} — {incident.service}
          <StatusBadge status={incident.status} />
        </h2>
        <RootCauseCard incident={incident} />
        <EvidenceGrid incident={incident} />
        <ImmediateFixCard incident={incident} />
        {incident.pending_suggestion && <KnowledgeSuggestionCard suggestion={incident.pending_suggestion} />}
      </div>
      <Timeline entries={incident.timeline} mode="latest" />
    </div>
  )
}
