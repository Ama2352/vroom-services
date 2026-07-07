import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Inbox } from 'lucide-react'
import { api } from '../api.js'
import { useApiResource } from '../hooks/useApiResource.js'
import StatusBadge from '../components/StatusBadge.jsx'
import ErrorBanner from '../components/ErrorBanner.jsx'
import EmptyState from '../components/EmptyState.jsx'
import { SkeletonTable } from '../components/Skeleton.jsx'

export default function PendingPage() {
  const [status, setStatus] = useState('pending')
  const { data, loading, error, reload } = useApiResource(
    () => api.get('/pending', { params: { status } }).then(r => r.data.pending),
    [status],
  )

  return (
    <div>
      <div className="tabs">
        <button className={status === 'pending' ? 'active' : ''} onClick={() => setStatus('pending')}>Pending</button>
        <button className={status === 'approved' ? 'active' : ''} onClick={() => setStatus('approved')}>Approved</button>
        <button className={status === 'rejected' ? 'active' : ''} onClick={() => setStatus('rejected')}>Rejected</button>
      </div>

      {error && <ErrorBanner message={error} onRetry={reload} />}
      {loading && !error && <SkeletonTable />}
      {!loading && !error && data.length === 0 && (
        <EmptyState message={`No ${status} suggestions.`} Icon={Inbox} />
      )}
      {!loading && !error && data.length > 0 && (
        <table className="data-table">
          <thead>
            <tr><th>Service</th><th>Symptom</th><th>Proposed key</th><th></th></tr>
          </thead>
          <tbody>
            {data.map(p => (
              <tr key={p.id}>
                <td>{p.service}</td>
                <td>{p.symptom}</td>
                <td>{p.proposed_knowledge_key}{p.is_new_knowledge_key && ' (new)'}</td>
                <td>
                  {status === 'pending'
                    ? <Link to={`/pending/${p.id}`} className="button secondary">Review</Link>
                    : <StatusBadge status={status} label={`${p.decided_by} @ ${new Date(p.decided_at * 1000).toLocaleString()}`} />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
