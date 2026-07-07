import { Link } from 'react-router-dom'
import { BookOpen } from 'lucide-react'
import { api } from '../api.js'
import { useApiResource } from '../hooks/useApiResource.js'
import ErrorBanner from '../components/ErrorBanner.jsx'
import EmptyState from '../components/EmptyState.jsx'
import { SkeletonTable } from '../components/Skeleton.jsx'

export default function KnowledgePage() {
  const { data, loading, error, reload } = useApiResource(
    () => api.get('/knowledge').then(r => r.data.knowledge),
    [],
  )

  if (error) return <ErrorBanner message={error} onRetry={reload} />
  if (loading) return <SkeletonTable />
  if (data.length === 0) return <EmptyState message="No knowledge entries yet." Icon={BookOpen} />

  return (
    <table className="data-table">
      <thead>
        <tr><th>Key</th><th>Root cause pattern</th><th>Conclusive</th><th>History count</th></tr>
      </thead>
      <tbody>
        {data.map(e => (
          <tr key={e.key}>
            <td><Link to={`/knowledge/${e.key}`}>{e.key}</Link></td>
            <td>{e.root_cause_pattern}</td>
            <td>{e.conclusive ? 'yes' : 'no'}</td>
            <td>{e.history_count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
