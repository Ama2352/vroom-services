import type { LucideIcon } from 'lucide-react'
import { Inbox } from 'lucide-react'

interface EmptyStateProps {
  message: string
  Icon?: LucideIcon
}

export function EmptyState({ message, Icon = Inbox }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-16 text-center text-ink-faint">
      <Icon size={32} className="opacity-50" />
      <p>{message}</p>
    </div>
  )
}
