import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Copy, Check } from 'lucide-react'
import StatusBadge from './StatusBadge.jsx'

export default function SuggestionSummary({ incident, suggestion }) {
  const [copied, setCopied] = useState(false)

  function copyHint() {
    navigator.clipboard.writeText(incident.kubectl_hint || '')
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className="card">
      <div className="card-title">Suggestion</div>
      <div className="root-cause-label">Dev action</div>
      <p>{incident.dev_action}</p>
      <div className="root-cause-label">Suggested command</div>
      <div className="code-block">
        <button className="copy-button" onClick={copyHint}>
          {copied ? <Check size={12} /> : <Copy size={12} />}
          {copied ? 'Copied' : 'Copy'}
        </button>
        {incident.kubectl_hint}
      </div>

      {suggestion && (
        <>
          <div className="suggestion-divider" />
          <p>{suggestion.symptom}</p>
          {suggestion.status === 'approved' ? (
            <div className="suggestion-approved">
              <StatusBadge status="approved" />
              <code className="suggestion-key">{suggestion.proposed_knowledge_key}</code>
            </div>
          ) : (
            <StatusBadge status={suggestion.status} />
          )}
          {suggestion.status === 'pending' && (
            <p><Link to={`/pending/${suggestion.id}`} className="button secondary">Review &amp; Decide</Link></p>
          )}
        </>
      )}
    </div>
  )
}
