import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Clock } from 'lucide-react'
import { api } from '../lib/api'
import { getActor } from '../lib/actor'
import { Card } from '../components/ui/Card'
import { Button } from '../components/ui/Button'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { EmptyState } from '../components/ui/EmptyState'
import { SkeletonTable } from '../components/ui/Skeleton'
import type { KnowledgeHistoryEntry } from '../types/knowledge'

const inputClasses = 'w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent-soft'
const labelClasses = 'mb-1 block text-sm font-medium text-ink-soft'

function HistoryRow({ entry, knowledgeKeys, onChanged }: {
  entry: KnowledgeHistoryEntry
  knowledgeKeys: string[]
  onChanged: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState({
    service: entry.service, symptom: entry.symptom,
    context_notes: entry.context_notes, knowledge_key: entry.knowledge_key,
  })
  const [saving, setSaving] = useState(false)

  function save() {
    setSaving(true)
    api.put(`/history/${entry.id}`, { actor: getActor(), ...form })
      .then(() => { setEditing(false); onChanged() })
      .finally(() => setSaving(false))
  }

  function del() {
    api.delete(`/history/${entry.id}`).then(onChanged)
  }

  if (editing) {
    return (
      <Card>
        <div className="mb-3">
          <label className={labelClasses}>Service</label>
          <input className={inputClasses} value={form.service}
                 onChange={e => setForm({ ...form, service: e.target.value })} />
        </div>
        <div className="mb-3">
          <label className={labelClasses}>Symptom</label>
          <textarea className={inputClasses} rows={2} value={form.symptom}
                     onChange={e => setForm({ ...form, symptom: e.target.value })} />
        </div>
        <div className="mb-3">
          <label className={labelClasses}>Context notes</label>
          <textarea className={inputClasses} rows={2} value={form.context_notes}
                     onChange={e => setForm({ ...form, context_notes: e.target.value })} />
        </div>
        <div className="mb-3">
          <label className={labelClasses}>Knowledge key</label>
          <select className={inputClasses} value={form.knowledge_key}
                  onChange={e => setForm({ ...form, knowledge_key: e.target.value })}>
            {knowledgeKeys.map(k => <option key={k} value={k}>{k}</option>)}
          </select>
        </div>
        <div className="flex gap-2">
          <Button onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save'}</Button>
          <Button variant="secondary" onClick={() => setEditing(false)}>Cancel</Button>
        </div>
      </Card>
    )
  }

  return (
    <Card>
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-ink-faint">
        <span>{new Date(Number(entry.timestamp) * 1000).toLocaleString()}</span>
        <span className="rounded-full bg-muted-soft px-2 py-0.5 text-muted">{entry.source}</span>
        <span className="ml-auto">{entry.created_by}</span>
      </div>
      <div className="mb-1 text-sm font-semibold text-ink">{entry.service}</div>
      <p className="mb-2 text-sm text-ink-soft">{entry.symptom}</p>
      <Link to={`/knowledge/${entry.knowledge_key}`} className="text-xs text-accent hover:text-accent-strong">
        {entry.knowledge_key}
      </Link>
      <div className="mt-3 flex gap-2">
        <Button variant="secondary" onClick={() => setEditing(true)}>Edit</Button>
        <Button variant="danger" onClick={del}>Delete</Button>
      </div>
    </Card>
  )
}

export function HistoryPage() {
  const [history, setHistory] = useState<KnowledgeHistoryEntry[] | undefined>(undefined)
  const [knowledgeKeys, setKnowledgeKeys] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  function load() {
    setError(null)
    Promise.all([
      api.get('/history').then(r => r.data.history as KnowledgeHistoryEntry[]),
      api.get('/knowledge').then(r => r.data.knowledge.map((k: { key: string }) => k.key)),
    ]).then(([h, keys]) => {
      setHistory([...h].sort((a, b) => Number(b.timestamp) - Number(a.timestamp)))
      setKnowledgeKeys(keys)
    }).catch(() => setError('Failed to load data from the incident-agent API.'))
  }

  useEffect(load, [])

  if (error) return <ErrorBanner message={error} onRetry={load} />
  if (!history) return <SkeletonTable />
  if (history.length === 0) return <EmptyState message="No history entries yet." Icon={Clock} />

  return (
    <div className="flex flex-col gap-3">
      {history.map(entry => (
        <HistoryRow key={entry.id} entry={entry} knowledgeKeys={knowledgeKeys} onChanged={load} />
      ))}
    </div>
  )
}
