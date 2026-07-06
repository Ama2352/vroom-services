import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api.js'
import { getActor } from '../actor.js'

export default function PendingDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [item, setItem] = useState(undefined)
  const [knowledgeKeys, setKnowledgeKeys] = useState([])
  const [mode, setMode] = useState('existing')
  const [form, setForm] = useState({
    knowledge_key: '', symptom: '', context_notes: '',
    root_cause_pattern: '', fix_action: '', conclusive: false,
  })

  useEffect(() => {
    api.get(`/pending/${id}`).then(r => {
      const p = r.data.pending
      setItem(p)
      setMode(p.is_new_knowledge_key ? 'new' : 'existing')
      setForm({
        knowledge_key: p.proposed_knowledge_key,
        symptom: p.symptom,
        context_notes: p.context_notes,
        root_cause_pattern: p.root_cause,
        fix_action: p.fix_action,
        conclusive: false,
      })
    })
    api.get('/knowledge').then(r => setKnowledgeKeys(r.data.knowledge.map(k => k.key)))
  }, [id])

  function approve() {
    api.post(`/pending/${id}/approve`, {
      actor: getActor(), mode, knowledge_key: form.knowledge_key,
      symptom: form.symptom, context_notes: form.context_notes,
      root_cause_pattern: form.root_cause_pattern, fix_action: form.fix_action,
      conclusive: form.conclusive,
    }).then(() => navigate('/pending'))
  }

  function reject() {
    api.post(`/pending/${id}/reject`, { actor: getActor() }).then(() => navigate('/pending'))
  }

  if (item === undefined) return <p>Loading…</p>

  return (
    <div className="card">
      <h2>Review Suggestion — {item.service}</h2>

      <label>
        <input type="radio" checked={mode === 'existing'} onChange={() => setMode('existing')} />
        {' '}Attach to existing key
      </label>
      <label>
        <input type="radio" checked={mode === 'new'} onChange={() => setMode('new')} />
        {' '}Create new key
      </label>

      {mode === 'existing' ? (
        <select value={form.knowledge_key} onChange={e => setForm({ ...form, knowledge_key: e.target.value })}>
          {knowledgeKeys.map(k => <option key={k} value={k}>{k}</option>)}
        </select>
      ) : (
        <input value={form.knowledge_key}
               onChange={e => setForm({ ...form, knowledge_key: e.target.value })}
               placeholder="new_key_slug" />
      )}

      <label>Symptom</label>
      <textarea value={form.symptom} onChange={e => setForm({ ...form, symptom: e.target.value })} />

      <label>Context notes</label>
      <textarea value={form.context_notes} onChange={e => setForm({ ...form, context_notes: e.target.value })} />

      {mode === 'new' && (
        <>
          <label>Root cause pattern</label>
          <textarea value={form.root_cause_pattern}
                    onChange={e => setForm({ ...form, root_cause_pattern: e.target.value })} />
          <label>Fix action</label>
          <textarea value={form.fix_action}
                    onChange={e => setForm({ ...form, fix_action: e.target.value })} />
          <label>
            <input type="checkbox" checked={form.conclusive}
                   onChange={e => setForm({ ...form, conclusive: e.target.checked })} />
            {' '}Conclusive (single-explanation failure)
          </label>
        </>
      )}

      <div className="row">
        <button className="button" onClick={approve}>Approve</button>
        <button className="button secondary" onClick={reject}>Reject</button>
      </div>
    </div>
  )
}
