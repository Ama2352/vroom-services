import { Link } from 'react-router-dom'

export default function SuggestionSummary({ suggestion }) {
  if (!suggestion) return null

  const statusLabel = {
    pending: 'Pending review',
    approved: `Approved into ${suggestion.proposed_knowledge_key}`,
    rejected: 'Rejected',
  }[suggestion.status] || suggestion.status

  return (
    <div className="card">
      <h3>Suggestion</h3>
      <p>{suggestion.symptom}</p>
      <p><strong>Status:</strong> {statusLabel}</p>
      {suggestion.status === 'pending' && (
        <Link to={`/pending/${suggestion.id}`} className="button">Review &amp; Decide</Link>
      )}
    </div>
  )
}
