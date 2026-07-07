import { Link } from 'react-router-dom'
import StatusBadge from './StatusBadge.jsx'

export default function SuggestionSummary({ suggestion }) {
  if (!suggestion) return null

  return (
    <div className="card">
      <div className="card-title">Suggestion</div>
      <p>{suggestion.symptom}</p>
      <StatusBadge
        status={suggestion.status}
        label={suggestion.status === 'approved' ? `Approved into ${suggestion.proposed_knowledge_key}` : undefined}
      />
      {suggestion.status === 'pending' && (
        <p><Link to={`/pending/${suggestion.id}`} className="button secondary">Review &amp; Decide</Link></p>
      )}
    </div>
  )
}
