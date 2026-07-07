import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Inbox } from 'lucide-react'
import { api } from '../api.js'
import { useApiResource } from '../hooks/useApiResource.js'
import ErrorBanner from '../components/ErrorBanner.jsx'
import EmptyState from '../components/EmptyState.jsx'
import { SkeletonTable } from '../components/Skeleton.jsx'

export default function IncidentsPage() {
  const [status, setStatus] = useState('open')
  const { data, loading, error, reload } = useApiResource(
    () => api.get('/incidents', { params: { status } }).then(r => r.data.incidents),
    [status],
  )

  return (
    <div>
      <div className="tabs">
        <button className={status === 'open' ? 'active' : ''} onClick={() => setStatus('open')}>Open</button>
        <button className={status === 'resolved' ? 'active' : ''} onClick={() => setStatus('resolved')}>Resolved</button>
      </div>

      {error && <ErrorBanner message={error} onRetry={reload} />}
      {loading && !error && <SkeletonTable />}
      {!loading && !error && data.length === 0 && (
        <EmptyState message={`No ${status} incidents.`} Icon={Inbox} />
      )}
      {!loading && !error && data.length > 0 && (
        <table className="data-table">
          <thead>
            <tr><th>Alert</th><th>Service</th><th>Root cause</th><th>Last activity</th></tr>
          </thead>
          <tbody>
            {data.map(i => (
              <tr key={i.id}>
                <td><Link to={`/incidents/${i.id}`}>{i.alert_name}</Link></td>
                <td>{i.service}</td>
                <td>{i.root_cause}</td>
                <td>{new Date(i.timestamp * 1000).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
