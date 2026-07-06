import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'

export default function IncidentsPage() {
  const [status, setStatus] = useState('open')
  const [incidents, setIncidents] = useState([])

  useEffect(() => {
    api.get('/incidents', { params: { status } }).then(r => setIncidents(r.data.incidents))
  }, [status])

  return (
    <div>
      <div className="tabs">
        <button className={status === 'open' ? 'active' : ''} onClick={() => setStatus('open')}>Open</button>
        <button className={status === 'resolved' ? 'active' : ''} onClick={() => setStatus('resolved')}>Resolved</button>
      </div>
      <table className="list-table">
        <thead>
          <tr><th>Alert</th><th>Service</th><th>Root cause</th><th>Last activity</th></tr>
        </thead>
        <tbody>
          {incidents.map(i => (
            <tr key={i.id}>
              <td><Link to={`/incidents/${i.id}`}>{i.alert_name}</Link></td>
              <td>{i.service}</td>
              <td>{i.root_cause}</td>
              <td>{new Date(i.timestamp * 1000).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
