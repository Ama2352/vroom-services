import { useState } from 'react'
import { Copy, Check } from 'lucide-react'
import StatusBadge from './StatusBadge.jsx'

export default function RootCauseCard({ incident }) {
  const [copied, setCopied] = useState(false)

  function copyHint() {
    navigator.clipboard.writeText(incident.kubectl_hint || '')
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div className="card root-cause-card">
      <div className="card-title">
        Root Cause
        {incident.low_confidence && <StatusBadge status="low-confidence" />}
      </div>
      <div className="root-cause-headline">{incident.root_cause}</div>
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
    </div>
  )
}
