import { useState } from 'react'
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight } from 'lucide-react'
import { groupTimeline, splitOccurrences } from '../utils/groupTimeline.js'

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

function PhaseGroup({ phase }) {
  const [expanded, setExpanded] = useState(false)
  const Icon = phase.Icon
  return (
    <div className="timeline-card timeline-card--phase">
      <div className="timeline-icon"><Icon size={12} /></div>
      <button className="phase-header" onClick={() => setExpanded(!expanded)}>
        <span className={`phase-status-dot phase-status-dot--${phase.status}`} />
        <span className="timeline-name">{phase.name}</span>
        <span className="duration-badge">{formatDuration(phase.durationMs)}</span>
        {expanded ? <ChevronDown size={14} className="phase-chevron" /> : <ChevronRight size={14} className="phase-chevron" />}
      </button>
      {expanded && (
        <div className="phase-children">
          {phase.steps.map((s, i) => {
            const metaEntries = Object.entries(s.metadata || {})
            return (
              <div key={i} className="phase-child-step">
                <span className="phase-child-name">{s.name}</span>
                <span className="duration-badge">{formatDuration(s.duration_ms)}</span>
                {metaEntries.length > 0 && (
                  <div className="timeline-meta">
                    {metaEntries.map(([k, v]) => (
                      <span key={k} className="timeline-meta-chip">{k}: {String(v)}</span>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function OccurrenceGroup({ entries, label }) {
  const items = groupTimeline(entries)
  return (
    <>
      {label && <div className="occurrence-divider">{label}</div>}
      {items.map((item, i) => {
        if (item.kind === 'fired') return <FiredCard key={i} entry={item.entry} />
        if (item.kind === 'resolved') return <ResolvedCard key={i} entry={item.entry} />
        if (item.kind === 'phase') return <PhaseGroup key={i} phase={item} />
        return null
      })}
    </>
  )
}

export default function Timeline({ entries, mode = 'full' }) {
  const occurrences = splitOccurrences(entries)
  const total = occurrences.length
  const shown = mode === 'latest' ? occurrences.slice(-1) : occurrences
  return (
    <div className="timeline-sidebar">
      <div className="card-title">
        Timeline
        {mode === 'latest' && total > 1 && (
          <span className="timeline-occurrence-note">latest of {total}</span>
        )}
      </div>
      <div className="timeline-rail">
        {shown.map((occ, i) => (
          <OccurrenceGroup
            key={i}
            entries={occ}
            label={mode === 'full' && total > 1 ? `Occurrence ${i + 1} of ${total}` : null}
          />
        ))}
      </div>
    </div>
  )
}
