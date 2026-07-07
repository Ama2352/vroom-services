import { cn } from '../../lib/cn'

interface StatusBadgeProps {
  status: string
  label?: string
}

const VARIANTS: Record<string, { bg: string; text: string; label: string }> = {
  open:             { bg: 'bg-critical-soft',    text: 'text-critical',         label: 'Open' },
  resolved:         { bg: 'bg-healthy-soft',     text: 'text-healthy',          label: 'Resolved' },
  pending:          { bg: 'bg-info-soft',        text: 'text-info',             label: 'Pending review' },
  approved:         { bg: 'bg-healthy-soft',     text: 'text-healthy',          label: 'Approved' },
  rejected:         { bg: 'bg-muted-soft',       text: 'text-muted',            label: 'Rejected' },
  'low-confidence': { bg: 'bg-root-cause-soft',  text: 'text-root-cause-label', label: 'Low confidence' },
}

export default function StatusBadge({ status, label }: StatusBadgeProps) {
  const variant = VARIANTS[status] ?? { bg: 'bg-muted-soft', text: 'text-muted', label: status }
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold', variant.bg, variant.text)}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {label ?? variant.label}
    </span>
  )
}
