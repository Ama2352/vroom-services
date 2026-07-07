import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import { getActor } from '../lib/actor'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { SkeletonCard } from '../components/ui/Skeleton'
import { Button } from '../components/ui/Button'
import type { PendingDetail } from '../types/pending'

const inputClasses = 'w-full rounded-md border border-border bg-white px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent-soft'
const labelClasses = 'mb-1 block text-sm font-medium text-ink-soft'

export function PendingDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [pending, setPending] = useState<PendingDetail | undefined>(undefined)
  const [knowledgeKeys, setKnowledgeKeys] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [mode, setMode] = useState<'existing' | 'new'>('existing')
  const [form, setForm] = useState({
    knowledge_key: '', symptom: '', context_notes: '',
    root_cause_pattern: '', fix_action: '', conclusive: false,
  })
  const [submitting, setSubmitting] = useState(false)

  function load() {
    setError(null)
    Promise.all([
      api.get(`/pending/${id}`).then(r => r.data.pending as PendingDetail),
      api.get('/knowledge').then(r => r.data.knowledge.map((k: { key: string }) => k.key)),
    ]).then(([p, keys]) => {
      setPending(p)
      setKnowledgeKeys(keys)
      setMode(p.is_new_knowledge_key ? 'new' : 'existing')
      setForm({
        knowledge_key: p.proposed_knowledge_key,
        symptom: p.symptom,
        context_notes: p.context_notes,
        root_cause_pattern: p.root_cause,
        fix_action: p.fix_action,
        conclusive: false,
      })
    }).catch(() => setError('Failed to load data from the incident-agent API.'))
  }

  useEffect(load, [id])

  function approve() {
    setSubmitting(true)
    api.post(`/pending/${id}/approve`, {
      actor: getActor(), mode, knowledge_key: form.knowledge_key,
      symptom: form.symptom, context_notes: form.context_notes,
      root_cause_pattern: form.root_cause_pattern, fix_action: form.fix_action,
      conclusive: form.conclusive,
    }).then(() => navigate('/pending')).finally(() => setSubmitting(false))
  }

  function reject() {
    setSubmitting(true)
    api.post(`/pending/${id}/reject`, { actor: getActor() })
      .then(() => navigate('/pending')).finally(() => setSubmitting(false))
  }

  if (error) return <ErrorBanner message={error} onRetry={load} />
  if (pending === undefined) return <SkeletonCard lines={6} />

  return (
    <div className="max-w-2xl rounded-[10px] border border-border bg-white p-4">
      <h2 className="mb-4 text-base font-bold text-ink">Review Suggestion — {pending.service}</h2>

      <div className="mb-2 flex items-center gap-2 text-sm text-ink-soft">
        <input type="radio" id="mode-existing" checked={mode === 'existing'} onChange={() => setMode('existing')} />
        <label htmlFor="mode-existing">Attach to existing key</label>
      </div>
      <div className="mb-4 flex items-center gap-2 text-sm text-ink-soft">
        <input type="radio" id="mode-new" checked={mode === 'new'} onChange={() => setMode('new')} />
        <label htmlFor="mode-new">Create new key</label>
      </div>

      <div className="mb-4">
        <label className={labelClasses}>Knowledge key</label>
        {mode === 'existing' ? (
          <select className={inputClasses} value={form.knowledge_key} onChange={e => setForm({ ...form, knowledge_key: e.target.value })}>
            {knowledgeKeys.map(k => <option key={k} value={k}>{k}</option>)}
          </select>
        ) : (
          <input className={inputClasses} value={form.knowledge_key}
                 onChange={e => setForm({ ...form, knowledge_key: e.target.value })}
                 placeholder="e.g. bad_dependency_address" />
        )}
      </div>

      <div className="mb-4">
        <label className={labelClasses}>Symptom</label>
        <textarea className={inputClasses} rows={3} value={form.symptom} onChange={e => setForm({ ...form, symptom: e.target.value })}
                  placeholder="e.g. ride-service repeatedly restarting; log shows dial tcp: lookup bad-host: no such host" />
        <div className="mt-1 text-xs text-ink-faint">Used to match future similar incidents — include distinctive error text.</div>
      </div>

      <div className="mb-4">
        <label className={labelClasses}>Context notes</label>
        <textarea className={inputClasses} rows={2} value={form.context_notes} onChange={e => setForm({ ...form, context_notes: e.target.value })}
                  placeholder="e.g. Confirmed manual kubectl set env hotfix during testing, not a real outage" />
      </div>

      {mode === 'new' && (
        <>
          <div className="mb-4">
            <label className={labelClasses}>Root cause pattern</label>
            <textarea className={inputClasses} rows={3} value={form.root_cause_pattern}
                      onChange={e => setForm({ ...form, root_cause_pattern: e.target.value })}
                      placeholder="e.g. A dependency address env var was changed to an invalid value, causing connection failures" />
          </div>
          <div className="mb-4">
            <label className={labelClasses}>Fix action</label>
            <textarea className={inputClasses} rows={3} value={form.fix_action}
                      onChange={e => setForm({ ...form, fix_action: e.target.value })}
                      placeholder="e.g. Check the ReplicaSet template diff and revert with kubectl set env ..." />
          </div>
          <div className="mb-4 flex items-center gap-2 text-sm text-ink-soft">
            <input type="checkbox" id="conclusive" checked={form.conclusive}
                   onChange={e => setForm({ ...form, conclusive: e.target.checked })} />
            <label htmlFor="conclusive">Conclusive (single-explanation failure)</label>
          </div>
        </>
      )}

      <div className="flex gap-2">
        <Button onClick={approve} disabled={submitting}>{submitting ? 'Submitting…' : 'Approve'}</Button>
        <Button variant="secondary" onClick={reject} disabled={submitting}>Reject</Button>
      </div>
    </div>
  )
}
