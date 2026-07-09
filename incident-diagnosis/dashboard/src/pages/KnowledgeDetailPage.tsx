import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Trash2 } from 'lucide-react'
import { api } from '../lib/api'
import { getActor } from '../lib/actor'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { SkeletonCard } from '../components/ui/Skeleton'
import { Button } from '../components/ui/Button'
import type { KnowledgeDetail } from '../types/knowledge'

const inputClasses = 'w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent-soft'
const labelClasses = 'mb-1 block text-sm font-medium text-ink-soft'

export function KnowledgeDetailPage() {
  const { key } = useParams()
  const [data, setData] = useState<KnowledgeDetail | undefined>(undefined)
  const [form, setForm] = useState<{ root_cause_pattern: string; fix_action: string; trigger_waiting_reason: string; conclusive: boolean } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  function load() {
    setError(null)
    api.get(`/knowledge/${key}`).then(r => {
      const d = r.data as KnowledgeDetail
      setData(d)
      setForm({
        root_cause_pattern: d.knowledge.root_cause_pattern,
        fix_action: d.knowledge.fix_action,
        trigger_waiting_reason: d.knowledge.trigger_waiting_reason || '',
        conclusive: d.knowledge.conclusive,
      })
    }).catch(() => setError('Failed to load data from the incident-agent API.'))
  }

  useEffect(load, [key])

  function save() {
    if (!form) return
    setSaving(true)
    api.put(`/knowledge/${key}`, { actor: getActor(), ...form }).then(load).finally(() => setSaving(false))
  }

  function deleteHistoryEntry(hid: string) {
    api.delete(`/history/${hid}`).then(load)
  }

  if (error) return <ErrorBanner message={error} onRetry={load} />
  if (!data || !form) return <SkeletonCard lines={6} />

  return (
    <div className="max-w-2xl space-y-4">
      <div className="rounded-[10px] border border-border bg-surface p-4">
        <h2 className="mb-4 text-base font-bold text-ink">{key}</h2>
        <div className="mb-4">
          <label className={labelClasses}>Trigger waiting reason</label>
          <input className={inputClasses} value={form.trigger_waiting_reason}
                 onChange={e => setForm({ ...form, trigger_waiting_reason: e.target.value })}
                 placeholder="e.g. OOMKilled, or Dependency:postgres:ZeroReplicas" />
          <div className="mt-1 text-xs text-ink-faint">Optional status signal for conclusive matching.</div>
        </div>
        <div className="mb-4">
          <label className={labelClasses}>Root cause pattern</label>
          <textarea className={inputClasses} rows={3} value={form.root_cause_pattern}
                    onChange={e => setForm({ ...form, root_cause_pattern: e.target.value })}
                    placeholder="e.g. Container repeatedly OOMKilled due to memory limit set below actual usage" />
        </div>
        <div className="mb-4">
          <label className={labelClasses}>Fix action</label>
          <textarea className={inputClasses} rows={3} value={form.fix_action}
                    onChange={e => setForm({ ...form, fix_action: e.target.value })}
                    placeholder="e.g. Raise the memory limit in the deployment manifest and redeploy" />
        </div>
        <div className="mb-4 flex items-center gap-2 text-sm text-ink-soft">
          <input type="checkbox" id="conclusive" checked={form.conclusive}
                 onChange={e => setForm({ ...form, conclusive: e.target.checked })} />
          <label htmlFor="conclusive">Conclusive</label>
        </div>
        <Button onClick={save} disabled={saving}>{saving ? 'Saving…' : 'Save'}</Button>
      </div>
      <div className="rounded-[10px] border border-border bg-surface p-4">
        <div className="mb-2 text-[10.5px] font-semibold uppercase tracking-wide text-accent">History entries</div>
        {data.history.length === 0 ? (
          <p className="text-xs text-ink-faint">No history entries yet.</p>
        ) : (
          <ul className="list-none space-y-2 p-0">
            {data.history.map(h => (
              <li key={h.id} className="flex items-center justify-between gap-3 text-sm text-ink-soft">
                <span>{h.service}: {h.symptom}</span>
                <Button variant="secondary" onClick={() => deleteHistoryEntry(h.id)}>
                  <Trash2 size={14} /> Delete
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
