import { Activity, RefreshCw, AlertTriangle, History } from 'lucide-react'
import type { Incident, Provenance } from '../../types/incident'
import { Card, CardTitle } from '../ui/Card'

function Row({ label, value }: { label: string; value: string | number | null | undefined }) {
  if (value === '' || value === null || value === undefined) return null
  return (
    <div className="flex justify-between gap-3 py-0.5 text-xs text-ink-soft">
      <dt>{label}</dt>
      <dd className="break-words font-mono text-ink">{String(value)}</dd>
    </div>
  )
}

function DiffBlock({ header, oldValue, newValue }: { header: string; oldValue?: string; newValue?: string }) {
  return (
    <div className="mb-3 last:mb-0">
      <div className="mb-1.5 font-mono text-xs text-ink-faint">{header}</div>
      <div className="overflow-hidden rounded-md border border-border font-mono text-[11px] leading-relaxed">
        <div className="whitespace-pre-wrap break-words bg-critical-soft px-3 py-1.5 text-critical">- {oldValue}</div>
        <div className="whitespace-pre-wrap break-words bg-healthy-soft px-3 py-1.5 text-healthy">+ {newValue}</div>
      </div>
    </div>
  )
}

function formatChangedAt(isoStr: string | null | undefined): string {
  if (!isoStr) return ''
  try {
    const d = new Date(isoStr)
    if (!isNaN(d.getTime())) {
      return d.toLocaleString()
    }
  } catch {}
  return isoStr
}

function ColoredDiffSnippet({ diff }: { diff: string }) {
  const lines = diff.split('\n')
  return (
    <pre className="overflow-x-auto rounded-md border border-border bg-canvas p-2.5 font-mono text-[11px] leading-relaxed">
      {lines.map((line, idx) => {
        let lineClass = 'text-ink-soft'
        if (line.startsWith('+') && !line.startsWith('+++')) {
          lineClass = 'text-healthy bg-healthy-soft px-1 rounded-sm'
        } else if (line.startsWith('-') && !line.startsWith('---')) {
          lineClass = 'text-critical bg-critical-soft px-1 rounded-sm'
        }
        return (
          <div key={idx} className={lineClass}>
            {line}
          </div>
        )
      })}
    </pre>
  )
}

function ProvenanceNote({ provenance }: { provenance: Provenance }) {
  if (provenance.classification === 'hotfix') {
    return (
      <div className="mt-3 rounded-md border border-root-cause bg-root-cause-soft px-3 py-2 text-xs text-root-cause-label font-medium">
        Manual change (not GitOps) — detected at {formatChangedAt(provenance.changed_at)}
      </div>
    )
  }
  if (!provenance.commit) {
    return (
      <div className="mt-2 rounded-md border border-border bg-canvas px-2.5 py-2 text-xs text-ink-faint">
        Change confirmed via GitOps but the originating commit wasn't found.
      </div>
    )
  }
  const { commit, pr } = provenance
  return (
    <div className="mt-2">
      <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-ink-soft">
        <a href={commit.url} target="_blank" rel="noreferrer" className="font-mono text-accent hover:text-accent-strong">
          {commit.sha}
        </a>
        <span>{commit.message}</span>
        <span className="text-ink-faint">by {commit.author}</span>
        {pr && (
          <a href={pr.url} target="_blank" rel="noreferrer"
             className="rounded-full bg-info-soft px-2 py-0.5 text-info hover:underline">
            PR #{pr.number}: {pr.title}
          </a>
        )}
      </div>
      {commit.diff_snippet && <ColoredDiffSnippet diff={commit.diff_snippet} />}
    </div>
  )
}

export function EvidenceGrid({ incident }: { incident: Incident }) {
  const td = incident.template_diff
  const dep = incident.dependency
  const prov = incident.provenance
  const hasInit = Boolean(incident.init_waiting_reason || incident.init_last_terminated_reason || incident.init_restarts > 0)
  const hasLogEvent = Boolean(incident.log_error || incident.event_reason)

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Card>
          <CardTitle><Activity size={14} /> Service Health ({incident.service})</CardTitle>
          <dl className="m-0">
            <Row label="Desired pods" value={incident.pods_desired} />
            <Row label="Running pods" value={incident.pods_running} />
            <Row label="Ready pods" value={incident.pods_ready} />
            <Row label="Waiting reason" value={incident.waiting_reason} />
            <Row label="Last terminated reason" value={incident.last_terminated_reason} />
            {incident.restarts > 0 && <Row label="Restarts" value={incident.restarts} />}
          </dl>
          {dep && dep.pods_desired > 0 && dep.pods_available === dep.pods_desired && !dep.waiting_reason && (
            <div className="mt-2 flex justify-between border-t border-dashed border-border pt-2 text-[11px] text-ink-faint">
              <span>Dependency: {dep.namespace}/{dep.name}</span>
              <span className="font-mono">{dep.pods_available}/{dep.pods_desired}</span>
            </div>
          )}
        </Card>

        {dep && (dep.pods_desired === 0 || dep.pods_available !== dep.pods_desired || dep.waiting_reason) && (
          <Card className="border-critical bg-critical-soft">
            <CardTitle className="text-critical">
              <AlertTriangle size={14} /> Dependency Unhealthy
            </CardTitle>
            <dl className="m-0">
              <Row label="Name" value={`${dep.namespace}/${dep.name}`} />
              <Row 
                label="Status" 
                value={dep.pods_desired === 0 ? "Scaled to 0 replicas (No pods running)" : `${dep.pods_available} of ${dep.pods_desired} pods ready`} 
              />
              {dep.waiting_reason && <Row label="Waiting reason" value={dep.waiting_reason} />}
            </dl>
          </Card>
        )}

        {hasInit && (
          <Card>
            <CardTitle><RefreshCw size={14} /> Init Container</CardTitle>
            <dl className="m-0">
              <Row label="Init waiting reason" value={incident.init_waiting_reason || "None"} />
              <Row label="Init last terminated reason" value={incident.init_last_terminated_reason || "None"} />
              {incident.init_restarts > 0 && <Row label="Init restarts" value={incident.init_restarts} />}
            </dl>
          </Card>
        )}
      </div>

      {(td || prov) && (
        <Card>
          <CardTitle><History size={14} /> Recent Change</CardTitle>
          {prov && prov.target === 'dependency' && prov.classification === 'hotfix' ? (
            <>
              <p className="text-xs text-ink-soft mb-3 leading-normal">
                Dependency <span className="font-semibold text-ink">{prov.dependency_name}</span> is currently <span className="font-semibold text-root-cause">OutOfSync</span> in ArgoCD due to manual scaling/configuration drift:
              </p>
              {prov.drift && prov.drift.map((item, idx) => (
                <DiffBlock
                  key={idx}
                  header={`${prov.dependency_name} · ${item.key}`}
                  oldValue={`Desired: ${item.correct}`}
                  newValue={`Actual: ${item.wrong}`}
                />
              ))}
              {prov.changed_at && (
                <div className="mt-3 flex justify-between border-t border-border pt-2.5 text-xs text-ink-soft">
                  <span>Changed at</span>
                  <span className="font-mono text-ink">
                    {formatChangedAt(prov.changed_at)}
                  </span>
                </div>
              )}
              <ProvenanceNote provenance={prov} />
            </>
          ) : (
            <>
              {td && (!prov || prov.classification === 'hotfix') && (
                <>
                  {td.env_changed && td.env_diff.map((d, i) => (
                    <DiffBlock key={i} header={`${incident.service} · env.${d.key}`} oldValue={d.old_value} newValue={d.new_value} />
                  ))}
                  {td.image_changed && (
                    <DiffBlock header={`${incident.service} · image`} oldValue={td.old_image} newValue={td.new_image} />
                  )}
                </>
              )}
              {((prov && prov.classification === 'gitops-commit' && prov.commit?.date) || (td && td.changed_at)) && (
                <div className="mt-3 flex justify-between border-t border-border pt-2.5 text-xs text-ink-soft">
                  <span>Changed at</span>
                  <span className="font-mono text-ink">
                    {formatChangedAt(
                      (prov?.classification === 'gitops-commit' && prov.commit?.date)
                        ? prov.commit.date
                        : (td ? td.changed_at : "")
                    )}
                  </span>
                </div>
              )}
              {prov && <ProvenanceNote provenance={prov} />}
            </>
          )}
        </Card>
      )}

      {hasLogEvent && (
        <Card>
          <CardTitle><AlertTriangle size={14} /> Log &amp; Event</CardTitle>
          <dl className="m-0">
            <Row label="Log error" value={incident.log_error} />
            <Row label="Event reason" value={incident.event_reason} />
            <Row label="Event message" value={incident.event_message} />
            <Row label="Event object" value={incident.event_object} />
          </dl>
        </Card>
      )}
    </div>
  )
}
