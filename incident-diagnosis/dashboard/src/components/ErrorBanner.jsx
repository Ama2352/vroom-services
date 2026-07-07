import { AlertTriangle, RefreshCw } from 'lucide-react'

export default function ErrorBanner({ message, onRetry }) {
  return (
    <div className="error-banner">
      <AlertTriangle size={16} />
      <span>{message}</span>
      {onRetry && (
        <button className="button secondary" onClick={onRetry}>
          <RefreshCw size={14} /> Retry
        </button>
      )}
    </div>
  )
}
