import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'

export default function KnowledgePage() {
  const [entries, setEntries] = useState([])

  useEffect(() => {
    api.get('/knowledge').then(r => setEntries(r.data.knowledge))
  }, [])

  return (
    <table className="list-table">
      <thead>
        <tr><th>Key</th><th>Root cause pattern</th><th>Conclusive</th><th>History count</th></tr>
      </thead>
      <tbody>
        {entries.map(e => (
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
