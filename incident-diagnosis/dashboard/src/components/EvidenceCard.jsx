import { Activity, RefreshCw, AlertTriangle, History, Server } from 'lucide-react'

function Row({ label, value }) {
  if (value === '' || value === null || value === undefined) return null
  return (
    <div className="evidence-row">
      <dt>{label}</dt>
      <dd>{String(value)}</dd>
    </div>
  )
}

function DiffBlock({ header, oldValue, newValue }) {
  return (
    <div>
      <div className="diff-header">{header}</div>
      <div className="diff-block">
        <div className="diff-row diff-row--removed">- {oldValue}</div>
        <div className="diff-row diff-row--added">+ {newValue}</div>
      </div>
    </div>
  )
}

export default function EvidenceCard({ incident }) {
  const td = incident.template_diff
  const dep = incident.dependency
  const hasInit = incident.init_waiting_reason || incident.init_last_terminated_reason || incident.init_restarts > 0
  const hasLogEvent = incident.log_error || incident.event_reason

  return (
    <div className="evidence-grid">
      <div className="card">
        <div className="card-title"><Activity size={14} /> Pod Health</div>
        <dl style={{ margin: 0 }}>
          <Row label="Pods available" value={incident.pods_available} />
          <Row label="Pods desired" value={incident.pods_desired} />
          <Row label="Waiting reason" value={incident.waiting_reason} />
          <Row label="Last terminated reason" value={incident.last_terminated_reason} />
          {incident.restarts > 0 && <Row label="Restarts" value={incident.restarts} />}
        </dl>
      </div>

      {hasInit && (
        <div className="card">
          <div className="card-title"><RefreshCw size={14} /> Init Container</div>
          <dl style={{ margin: 0 }}>
            <Row label="Init waiting reason" value={incident.init_waiting_reason} />
            <Row label="Init last terminated reason" value={incident.init_last_terminated_reason} />
            {incident.init_restarts > 0 && <Row label="Init restarts" value={incident.init_restarts} />}
          </dl>
        </div>
      )}

      {hasLogEvent && (
        <div className="card">
          <div className="card-title"><AlertTriangle size={14} /> Log & Event</div>
          <dl style={{ margin: 0 }}>
            <Row label="Log error" value={incident.log_error} />
            <Row label="Event reason" value={incident.event_reason} />
            <Row label="Event message" value={incident.event_message} />
            <Row label="Event object" value={incident.event_object} />
          </dl>
        </div>
      )}

      {td && (
        <div className="card">
          <div className="card-title"><History size={14} /> Recent Change</div>
          {td.env_changed && td.env_diff.map((d, i) => (
            <DiffBlock key={i} header={`${incident.service} · env.${d.key}`} oldValue={d.old_value} newValue={d.new_value} />
          ))}
          {td.image_changed && (
            <DiffBlock header={`${incident.service} · image`} oldValue={td.old_image} newValue={td.new_image} />
          )}
          <Row label="Changed at" value={td.changed_at} />
        </div>
      )}

      {dep && (
        <div className="card">
          <div className="card-title"><Server size={14} /> Dependency</div>
          <dl style={{ margin: 0 }}>
            <Row label="Name" value={`${dep.namespace}/${dep.name}`} />
            <Row label="Pods" value={`${dep.pods_available}/${dep.pods_desired}`} />
            <Row label="Waiting reason" value={dep.waiting_reason} />
          </dl>
        </div>
      )}
    </div>
  )
}
