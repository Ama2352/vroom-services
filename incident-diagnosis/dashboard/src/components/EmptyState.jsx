import { Inbox } from 'lucide-react'

export default function EmptyState({ message, Icon = Inbox }) {
  return (
    <div className="empty-state">
      <Icon size={32} />
      <p>{message}</p>
    </div>
  )
}
