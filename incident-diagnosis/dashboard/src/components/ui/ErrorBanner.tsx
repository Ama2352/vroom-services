import { AlertTriangle, RefreshCw } from 'lucide-react'
import { buttonClasses } from './Button'

interface ErrorBannerProps {
  message: string
  onRetry?: () => void
}

export function ErrorBanner({ message, onRetry }: ErrorBannerProps) {
  return (
    <div className="mb-4 flex items-center gap-3 rounded-[10px] bg-critical-soft px-4 py-3 text-sm text-critical">
      <AlertTriangle size={16} />
      <span>{message}</span>
      {onRetry && (
        <button className={buttonClasses('secondary', 'ml-auto')} onClick={onRetry}>
          <RefreshCw size={14} /> Retry
        </button>
      )}
    </div>
  )
}
