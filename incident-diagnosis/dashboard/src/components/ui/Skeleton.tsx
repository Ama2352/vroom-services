import type { CSSProperties } from 'react'
import { cn } from '../../lib/cn'

interface SkeletonProps {
  height?: number | string
  width?: number | string
  className?: string
  style?: CSSProperties
}

export function Skeleton({ height = 16, width = '100%', className, style }: SkeletonProps) {
  return <div className={cn('animate-pulse rounded-md bg-slate-700', className)} style={{ height, width, ...style }} />
}

export function SkeletonCard({ lines = 3 }: { lines?: number }) {
  return (
    <div className="rounded-[10px] border border-border bg-surface p-4">
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} height={14} width={i === lines - 1 ? '60%' : '100%'} className="mb-2.5" />
      ))}
    </div>
  )
}

export function SkeletonTable({ rows = 4 }: { rows?: number }) {
  return (
    <div className="rounded-[10px] border border-border bg-surface p-4">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} height={18} className="mb-2.5" />
      ))}
    </div>
  )
}
