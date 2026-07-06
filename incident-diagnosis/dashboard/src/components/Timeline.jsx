export default function Timeline({ entries }) {
  return (
    <div className="card">
      <h3>Timeline</h3>
      <ul className="timeline">
        {entries.map((e, i) => (
          <li key={i}>
            {e.type === 'fired' ? 'Fired' : 'Resolved'}
            {' — '}{new Date(e.timestamp * 1000).toLocaleString()}
            {e.type === 'resolved' && e.actor && ` by ${e.actor}`}
            {e.type === 'fired' && e.evidence_snapshot?.waiting_reason &&
              ` — ${e.evidence_snapshot.waiting_reason}`}
          </li>
        ))}
      </ul>
    </div>
  )
}
