export default function Skeleton({ height = 16, width = '100%', style }) {
  return <div className="skeleton" style={{ height, width, ...style }} />
}

export function SkeletonCard({ lines = 3 }) {
  return (
    <div className="card">
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} height={14} width={i === lines - 1 ? '60%' : '100%'} style={{ marginBottom: 10 }} />
      ))}
    </div>
  )
}

export function SkeletonTable({ rows = 4 }) {
  return (
    <div className="card">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} height={18} style={{ marginBottom: 10 }} />
      ))}
    </div>
  )
}
