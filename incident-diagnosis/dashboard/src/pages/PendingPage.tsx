import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Inbox } from 'lucide-react'
import { api } from '../lib/api'
import { useApiResource } from '../hooks/useApiResource'
import StatusBadge from '../components/ui/Badge'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { EmptyState } from '../components/ui/EmptyState'
import { SkeletonTable } from '../components/ui/Skeleton'
import { Tabs } from '../components/ui/Tabs'
import { Table, Th, Td, Tr } from '../components/ui/Table'
import { buttonClasses } from '../components/ui/Button'
import type { PendingListItem, PendingStatus } from '../types/pending'
import { formatTimestamp } from '../lib/format'

export function PendingPage() {
  const [status, setStatus] = useState<PendingStatus>('pending')
  const { data, loading, error, reload } = useApiResource<PendingListItem[]>(
    () => api.get('/pending', { params: { status } }).then(r => r.data.pending),
    [status],
  )

  return (
    <div>
      <Tabs
        value={status}
        onChange={setStatus}
        options={[
          { value: 'pending', label: 'Pending' },
          { value: 'approved', label: 'Approved' },
          { value: 'rejected', label: 'Rejected' },
        ]}
      />

      {error && <ErrorBanner message={error} onRetry={reload} />}
      {loading && !error && <SkeletonTable />}
      {!loading && !error && data?.length === 0 && (
        <EmptyState message={`No ${status} suggestions.`} Icon={Inbox} />
      )}
      {!loading && !error && data && data.length > 0 && (
        <Table>
          <thead>
            <tr><Th>Service</Th><Th>Symptom</Th><Th>Proposed key</Th><Th /></tr>
          </thead>
          <tbody>
            {data.map(p => (
              <Tr key={p.id}>
                <Td>{p.service}</Td>
                <Td>{p.symptom}</Td>
                <Td>{p.proposed_knowledge_key}{p.is_new_knowledge_key && ' (new)'}</Td>
                <Td>
                  {status === 'pending'
                    ? <Link to={`/pending/${p.id}`} className={buttonClasses('secondary')}>Review</Link>
                    : <StatusBadge status={status} label={`${p.decided_by} @ ${p.decided_at ? formatTimestamp(p.decided_at) : ''}`} />}
                </Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  )
}
