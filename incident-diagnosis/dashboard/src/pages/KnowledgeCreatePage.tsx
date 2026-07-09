import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { getActor } from '../lib/actor'
import { Button } from '../components/ui/Button'

const inputClasses = 'w-full rounded-md border border-border bg-surface px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent-soft'
const labelClasses = 'mb-1 block text-sm font-medium text-ink-soft'

export function KnowledgeCreatePage() {
  const navigate = useNavigate()
  const [form, setForm] = useState({ key: '', root_cause_pattern: '', fix_action: '', trigger_waiting_reason: '', conclusive: false })
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  function create() {
    setSubmitting(true)
    setError(null)
    api.post('/knowledge', { actor: getActor(), ...form })
      .then(() => navigate(`/knowledge/${form.key}`))
      .catch(e => setError(e.response?.data?.error || 'Failed to create knowledge entry.'))
      .finally(() => setSubmitting(false))
  }

  return (
    <div className="max-w-2xl rounded-[10px] border border-border bg-surface p-4">
      <h2 className="mb-4 text-base font-bold text-ink">New Knowledge Entry</h2>

      {error && <p className="mb-4 rounded-md bg-critical-soft px-3 py-2 text-sm text-critical">{error}</p>}

      <div className="mb-4">
        <label className={labelClasses}>Key</label>
        <input className={inputClasses} value={form.key}
               onChange={e => setForm({ ...form, key: e.target.value })}
               placeholder="e.g. bad_dependency_address" />
        <div className="mt-1 text-xs text-ink-faint">snake_case, must be unique.</div>
      </div>

      <div className="mb-4">
        <label className={labelClasses}>Trigger waiting reason</label>
        <input className={inputClasses} value={form.trigger_waiting_reason}
               onChange={e => setForm({ ...form, trigger_waiting_reason: e.target.value })}
               placeholder="e.g. OOMKilled, or Dependency:postgres:ZeroReplicas" />
        <div className="mt-1 text-xs text-ink-faint">Optional. Conclusive matching uses this status signal to link incidents automatically.</div>
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
        <label htmlFor="conclusive">Conclusive (single-explanation failure)</label>
      </div>

      <Button onClick={create} disabled={submitting || !form.key}>
        {submitting ? 'Creating…' : 'Create'}
      </Button>
    </div>
  )
}
