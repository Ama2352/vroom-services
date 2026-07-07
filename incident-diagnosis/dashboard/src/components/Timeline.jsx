import { AlertTriangle, Zap, CheckCircle2 } from 'lucide-react'

function formatDuration(ms) {
  if (ms == null) return ''
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

function formatTimestamp(ts) {
  return new Date(ts * 1000).toLocaleString()
}

function FiredCard({ entry }) {
  const reason = entry.evidence_snapshot?.waiting_reason
  return (
    <div className="timeline-card timeline-card--fired">
      <div className="timeline-icon"><AlertTriangle size={12} /></div>
      <div className="timeline-header">
        <span className="timeline-name">Fired</span>
        <span className="timeline-timestamp">{formatTimestamp(entry.timestamp)}</span>
      </div>
      {reason && (
        <div className="timeline-meta">
          <span className="timeline-meta-chip">waiting_reason: {reason}</span>
        </div>
      )}
    </div>
  )
}

function StepCard({ entry }) {
  const metaEntries = Object.entries(entry.metadata || {})
  return (
    <div className="timeline-card timeline-card--step">
      <div className="timeline-icon"><Zap size={12} /></div>
      <div className="timeline-header">
        <span className="timeline-name">{entry.name}</span>
        <span className="duration-badge">{formatDuration(entry.duration_ms)}</span>
        <span className="timeline-timestamp">{formatTimestamp(entry.started_at)}</span>
      </div>
      {metaEntries.length > 0 && (
        <div className="timeline-meta">
          {metaEntries.map(([k, v]) => (
            <span key={k} className="timeline-meta-chip">{k}: {String(v)}</span>
          ))}
        </div>
      )}
    </div>
  )
}

function ResolvedCard({ entry }) {
  return (
    <div className="timeline-card timeline-card--resolved">
      <div className="timeline-icon"><CheckCircle2 size={12} /></div>
      <div className="timeline-header">
        <span className="timeline-name">Resolved</span>
        <span className="timeline-timestamp">{formatTimestamp(entry.timestamp)}</span>
      </div>
      {entry.actor && (
        <div className="timeline-meta">
          <span className="timeline-meta-chip">by {entry.actor}</span>
        </div>
      )}
    </div>
  )
}

export default function Timeline({ entries }) {
  return (
    <div className="card">
      <div className="card-title">Timeline</div>
      <div className="timeline-rail">
        {entries.map((e, i) => {
          if (e.type === 'fired') return <FiredCard key={i} entry={e} />
          if (e.type === 'step') return <StepCard key={i} entry={e} />
          if (e.type === 'resolved') return <ResolvedCard key={i} entry={e} />
          return null
        })}
      </div>
    </div>
  )
}
