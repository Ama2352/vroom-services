import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Trash2 } from 'lucide-react'
import { api } from '../api.js'
import { getActor } from '../actor.js'
import ErrorBanner from '../components/ErrorBanner.jsx'
import { SkeletonCard } from '../components/Skeleton.jsx'

export default function KnowledgeDetailPage() {
  const { key } = useParams()
  const [data, setData] = useState(undefined)
  const [form, setForm] = useState(null)
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)

  function load() {
    setError(null)
    api.get(`/knowledge/${key}`).then(r => {
      setData(r.data)
      setForm({
        root_cause_pattern: r.data.knowledge.root_cause_pattern,
        fix_action: r.data.knowledge.fix_action,
        conclusive: r.data.knowledge.conclusive,
      })
    }).catch(() => setError('Failed to load data from the incident-agent API.'))
  }

  useEffect(load, [key])

  function save() {
    setSaving(true)
    api.put(`/knowledge/${key}`, { actor: getActor(), ...form }).then(load).finally(() => setSaving(false))
  }

  function deleteHistoryEntry(hid) {
    api.delete(`/history/${hid}`).then(load)
  }

  if (error) return <ErrorBanner message={error} onRetry={load} />
  if (!data || !form) return <SkeletonCard lines={6} />

  return (
    <div>
      <div className="card">
        <h2>{key}</h2>
        <div className="field">
          <label>Root cause pattern</label>
          <textarea value={form.root_cause_pattern}
                    onChange={e => setForm({ ...form, root_cause_pattern: e.target.value })}
                    placeholder="e.g. Container repeatedly OOMKilled due to memory limit set below actual usage" />
        </div>
        <div className="field">
          <label>Fix action</label>
          <textarea value={form.fix_action}
                    onChange={e => setForm({ ...form, fix_action: e.target.value })}
                    placeholder="e.g. Raise the memory limit in the deployment manifest and redeploy" />
        </div>
        <div className="checkbox-row">
          <input type="checkbox" id="conclusive" checked={form.conclusive}
                 onChange={e => setForm({ ...form, conclusive: e.target.checked })} />
          <label htmlFor="conclusive">Conclusive</label>
        </div>
        <button className="button" onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
      </div>
      <div className="card">
        <div className="card-title">History entries</div>
        {data.history.length === 0 ? (
          <p className="field-hint">No history entries yet.</p>
        ) : (
          <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
            {data.history.map(h => (
              <li key={h.id} className="evidence-row" style={{ justifyContent: 'space-between' }}>
                <span>{h.service}: {h.symptom}</span>
                <button className="button secondary" onClick={() => deleteHistoryEntry(h.id)}>
                  <Trash2 size={14} /> Delete
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
