import StatusBadge from './StatusBadge.jsx'

export default function RootCauseCard({ incident }) {
  return (
    <div className="card root-cause-card">
      <div className="card-title">
        Root Cause
        {incident.low_confidence && <StatusBadge status="low-confidence" />}
      </div>
      <div className="root-cause-headline">{incident.root_cause}</div>
    </div>
  )
}
