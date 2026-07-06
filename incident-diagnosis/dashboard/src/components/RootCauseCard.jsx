export default function RootCauseCard({ incident }) {
  return (
    <div className="card">
      <h3>
        Root Cause
        {incident.low_confidence && <span className="badge warn">Low confidence</span>}
      </h3>
      <p><strong>Root cause:</strong> {incident.root_cause}</p>
      <p><strong>Dev action:</strong> {incident.dev_action}</p>
      <p><strong>Suggested command:</strong></p>
      <pre className="code-block">{incident.kubectl_hint}</pre>
    </div>
  )
}
