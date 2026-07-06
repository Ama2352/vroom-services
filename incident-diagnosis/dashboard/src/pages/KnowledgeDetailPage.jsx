import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../api.js'
import { getActor } from '../actor.js'

export default function KnowledgeDetailPage() {
  const { key } = useParams()
  const [data, setData] = useState(undefined)
  const [form, setForm] = useState(null)

  function load() {
    api.get(`/knowledge/${key}`).then(r => {
      setData(r.data)
      setForm({
        root_cause_pattern: r.data.knowledge.root_cause_pattern,
        fix_action: r.data.knowledge.fix_action,
        conclusive: r.data.knowledge.conclusive,
      })
    })
  }

  useEffect(load, [key])

  function save() {
    api.put(`/knowledge/${key}`, { actor: getActor(), ...form }).then(load)
  }

  function deleteHistoryEntry(hid) {
    api.delete(`/history/${hid}`).then(load)
  }

  if (!data || !form) return <p>Loading…</p>

  return (
    <div>
      <div className="card">
        <h2>{key}</h2>
        <label>Root cause pattern</label>
        <textarea value={form.root_cause_pattern}
                  onChange={e => setForm({ ...form, root_cause_pattern: e.target.value })} />
        <label>Fix action</label>
        <textarea value={form.fix_action}
                  onChange={e => setForm({ ...form, fix_action: e.target.value })} />
        <label>
          <input type="checkbox" checked={form.conclusive}
                 onChange={e => setForm({ ...form, conclusive: e.target.checked })} />
          {' '}Conclusive
        </label>
        <button className="button" onClick={save}>Save</button>
      </div>
      <div className="card">
        <h3>History entries</h3>
        <ul>
          {data.history.map(h => (
            <li key={h.id}>
              {h.service}: {h.symptom}{' '}
              <button className="button secondary" onClick={() => deleteHistoryEntry(h.id)}>Delete</button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}
