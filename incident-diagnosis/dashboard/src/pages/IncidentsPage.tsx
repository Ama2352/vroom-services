import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Inbox } from 'lucide-react'
import { api } from '../lib/api'
import { useApiResource } from '../hooks/useApiResource'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { EmptyState } from '../components/ui/EmptyState'
import { SkeletonTable } from '../components/ui/Skeleton'
import { Tabs } from '../components/ui/Tabs'
import { Table, Th, Td, Tr } from '../components/ui/Table'
import type { IncidentListItem, IncidentStatus } from '../types/incident'
import { formatTimestamp } from '../lib/format'

export function IncidentsPage() {
  const [status, setStatus] = useState<IncidentStatus>('open')
  const { data, loading, error, reload } = useApiResource<IncidentListItem[]>(
    () => api.get('/incidents', { params: { status } }).then(r => r.data.incidents),
    [status],
  )

  return (
    <div>
      <Tabs
        value={status}
        onChange={setStatus}
        options={[
          { value: 'open', label: 'Open' },
          { value: 'resolved', label: 'Resolved' },
        ]}
      />

      {error && <ErrorBanner message={error} onRetry={reload} />}
      {loading && !error && <SkeletonTable />}
      {!loading && !error && data?.length === 0 && (
        <EmptyState message={`No ${status} incidents.`} Icon={Inbox} />
      )}
      {!loading && !error && data && data.length > 0 && (
        <Table>
          <thead>
            <tr><Th>Alert</Th><Th>Service</Th><Th>Root cause</Th><Th>Last activity</Th></tr>
          </thead>
          <tbody>
            {data.map(i => (
              <Tr key={i.id}>
                <Td><Link to={`/incidents/${i.id}`} className="font-semibold text-accent hover:text-accent-strong">{i.alert_name}</Link></Td>
                <Td>{i.service}</Td>
                <Td>{i.root_cause}</Td>
                <Td>{formatTimestamp(i.timestamp)}</Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  )
}
