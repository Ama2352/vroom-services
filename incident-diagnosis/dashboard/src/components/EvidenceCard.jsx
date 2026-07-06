const LABELS = [
  ['pods_available', 'Pods available'],
  ['pods_desired', 'Pods desired'],
  ['waiting_reason', 'Waiting reason'],
  ['last_terminated_reason', 'Last terminated reason'],
  ['restarts', 'Restarts'],
  ['init_waiting_reason', 'Init waiting reason'],
  ['init_last_terminated_reason', 'Init last terminated reason'],
  ['init_restarts', 'Init restarts'],
  ['log_error', 'Log error'],
  ['event_reason', 'Event reason'],
  ['event_message', 'Event message'],
  ['event_object', 'Event object'],
]

export default function EvidenceCard({ incident }) {
  return (
    <div className="card">
      <h3>Evidence</h3>
      <dl className="evidence-list">
        {LABELS.map(([field, label]) => {
          const value = incident[field]
          if (value === '' || value === null || value === undefined) return null
          if ((field === 'restarts' || field === 'init_restarts') && value === 0) return null
          return (
            <div key={field} className="evidence-row">
              <dt>{label}</dt>
              <dd>{String(value)}</dd>
            </div>
          )
        })}
      </dl>
    </div>
  )
}
