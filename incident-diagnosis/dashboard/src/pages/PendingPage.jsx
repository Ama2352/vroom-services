import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'

export default function PendingPage() {
  const [status, setStatus] = useState('pending')
  const [items, setItems] = useState([])

  useEffect(() => {
    api.get('/pending', { params: { status } }).then(r => setItems(r.data.pending))
  }, [status])

  return (
    <div>
      <div className="tabs">
        <button className={status === 'pending' ? 'active' : ''} onClick={() => setStatus('pending')}>Pending</button>
        <button className={status === 'approved' ? 'active' : ''} onClick={() => setStatus('approved')}>Approved</button>
        <button className={status === 'rejected' ? 'active' : ''} onClick={() => setStatus('rejected')}>Rejected</button>
      </div>
      <table className="list-table">
        <thead>
          <tr><th>Service</th><th>Symptom</th><th>Proposed key</th><th></th></tr>
        </thead>
        <tbody>
          {items.map(p => (
            <tr key={p.id}>
              <td>{p.service}</td>
              <td>{p.symptom}</td>
              <td>{p.proposed_knowledge_key}{p.is_new_knowledge_key && ' (new)'}</td>
              <td>
                {status === 'pending'
                  ? <Link to={`/pending/${p.id}`}>Review</Link>
                  : `${p.decided_by} @ ${new Date(p.decided_at * 1000).toLocaleString()}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
