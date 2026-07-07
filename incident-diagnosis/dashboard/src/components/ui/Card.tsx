import type { ReactNode } from 'react'
import { cn } from '../../lib/cn'

interface CardProps {
  children: ReactNode
  className?: string
}

export function Card({ children, className }: CardProps) {
  return <div className={cn('rounded-[10px] border border-border bg-white p-3', className)}>{children}</div>
}

export function CardTitle({ children, className }: CardProps) {
  return (
    <div className={cn('mb-2 flex items-center gap-2 text-[10.5px] font-semibold uppercase tracking-wide text-accent', className)}>
      {children}
    </div>
  )
}
