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

function Group({ title, Icon, children }) {
  return (
    <div className="evidence-group">
      <div className="evidence-group-title"><Icon size={14} /> {title}</div>
      <dl style={{ margin: 0 }}>{children}</dl>
    </div>
  )
}

export default function EvidenceCard({ incident }) {
  const td = incident.template_diff
  const dep = incident.dependency
  const hasInit = incident.init_waiting_reason || incident.init_last_terminated_reason || incident.init_restarts > 0
  const hasLogEvent = incident.log_error || incident.event_reason

  return (
    <div className="card">
      <div className="card-title">Evidence</div>

      <Group title="Pod Health" Icon={Activity}>
        <Row label="Pods available" value={incident.pods_available} />
        <Row label="Pods desired" value={incident.pods_desired} />
        <Row label="Waiting reason" value={incident.waiting_reason} />
        <Row label="Last terminated reason" value={incident.last_terminated_reason} />
        {incident.restarts > 0 && <Row label="Restarts" value={incident.restarts} />}
      </Group>

      {hasInit && (
        <Group title="Init Container" Icon={RefreshCw}>
          <Row label="Init waiting reason" value={incident.init_waiting_reason} />
          <Row label="Init last terminated reason" value={incident.init_last_terminated_reason} />
          {incident.init_restarts > 0 && <Row label="Init restarts" value={incident.init_restarts} />}
        </Group>
      )}

      {hasLogEvent && (
        <Group title="Log & Event" Icon={AlertTriangle}>
          <Row label="Log error" value={incident.log_error} />
          <Row label="Event reason" value={incident.event_reason} />
          <Row label="Event message" value={incident.event_message} />
          <Row label="Event object" value={incident.event_object} />
        </Group>
      )}

      {td && (
        <Group title="Recent Change" Icon={History}>
          {td.env_changed && td.env_diff.map((d, i) => (
            <Row key={i} label={`env ${d.key}`} value={`${d.old_value} → ${d.new_value}`} />
          ))}
          {td.image_changed && <Row label="image" value={`${td.old_image} → ${td.new_image}`} />}
          <Row label="Changed at" value={td.changed_at} />
        </Group>
      )}

      {dep && (
        <Group title="Dependency" Icon={Server}>
          <Row label="Name" value={`${dep.namespace}/${dep.name}`} />
          <Row label="Pods" value={`${dep.pods_available}/${dep.pods_desired}`} />
          <Row label="Waiting reason" value={dep.waiting_reason} />
        </Group>
      )}
    </div>
  )
}
