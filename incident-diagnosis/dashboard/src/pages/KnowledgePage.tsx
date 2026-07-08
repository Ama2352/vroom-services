import { Link } from 'react-router-dom'
import { BookOpen } from 'lucide-react'
import { api } from '../lib/api'
import { useApiResource } from '../hooks/useApiResource'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { EmptyState } from '../components/ui/EmptyState'
import { SkeletonTable } from '../components/ui/Skeleton'
import { Table, Th, Td, Tr } from '../components/ui/Table'
import { buttonClasses } from '../components/ui/Button'
import type { KnowledgeListItem } from '../types/knowledge'

export function KnowledgePage() {
  const { data, loading, error, reload } = useApiResource<KnowledgeListItem[]>(
    () => api.get('/knowledge').then(r => r.data.knowledge),
    [],
  )

  return (
    <div>
      <div className="mb-4 flex items-center justify-end">
        <Link to="/knowledge/new" className={buttonClasses('primary')}>+ New Entry</Link>
      </div>
      {error && <ErrorBanner message={error} onRetry={reload} />}
      {!error && (loading || !data) && <SkeletonTable />}
      {!error && !loading && data && data.length === 0 && (
        <EmptyState message="No knowledge entries yet." Icon={BookOpen} />
      )}
      {!error && !loading && data && data.length > 0 && (
        <Table>
          <thead>
            <tr><Th>Key</Th><Th>Root cause pattern</Th><Th>Conclusive</Th><Th>History count</Th></tr>
          </thead>
          <tbody>
            {data.map(e => (
              <Tr key={e.key}>
                <Td><Link to={`/knowledge/${e.key}`} className="font-semibold text-accent hover:text-accent-strong">{e.key}</Link></Td>
                <Td>{e.root_cause_pattern}</Td>
                <Td>{e.conclusive ? 'yes' : 'no'}</Td>
                <Td>{e.history_count}</Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  )
}
