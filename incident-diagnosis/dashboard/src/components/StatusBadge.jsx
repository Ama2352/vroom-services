import { AlertTriangle, CheckCircle2, Clock, XCircle, Inbox } from 'lucide-react'

const VARIANTS = {
  open:             { color: 'critical', label: 'Open',           Icon: AlertTriangle },
  resolved:         { color: 'healthy',  label: 'Resolved',       Icon: CheckCircle2 },
  pending:          { color: 'info',     label: 'Pending review', Icon: Clock },
  approved:         { color: 'healthy',  label: 'Approved',       Icon: CheckCircle2 },
  rejected:         { color: 'muted',    label: 'Rejected',       Icon: XCircle },
  'low-confidence': { color: 'warning',  label: 'Low confidence', Icon: AlertTriangle },
}

export default function StatusBadge({ status, label }) {
  const variant = VARIANTS[status] || { color: 'muted', label: status, Icon: Inbox }
  const Icon = variant.Icon
  return (
    <span className={`status-pill status-pill--${variant.color}`}>
      <Icon size={12} strokeWidth={2.5} />
      {label || variant.label}
    </span>
  )
}
