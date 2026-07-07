import type { ReactNode, ThHTMLAttributes, TdHTMLAttributes, HTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-[10px] border border-border bg-white">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  )
}

export function Th({ className, ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn('border-b border-border px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-ink-faint', className)}
      {...props}
    />
  )
}

export function Td({ className, ...props }: TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td
      className={cn('border-b border-border px-4 py-2.5 text-ink-soft [tr:last-child_&]:border-b-0', className)}
      {...props}
    />
  )
}

export function Tr({ className, ...props }: HTMLAttributes<HTMLTableRowElement>) {
  return <tr className={cn('hover:bg-slate-50', className)} {...props} />
}
