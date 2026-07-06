import { useEffect, useState } from 'react'
import EvidenceCard from '../components/EvidenceCard.jsx'
import RootCauseCard from '../components/RootCauseCard.jsx'
import SuggestionSummary from '../components/SuggestionSummary.jsx'
import Timeline from '../components/Timeline.jsx'
import { api } from '../api.js'

const POLL_MS = 10000

export default function LivePage() {
  const [incident, setIncident] = useState(undefined)

  useEffect(() => {
    let cancelled = false
    function load() {
      api.get('/incidents/latest').then(r => {
        if (!cancelled) setIncident(r.data.incident)
      }).catch(() => {})
    }
    load()
    const interval = setInterval(load, POLL_MS)
    return () => { cancelled = true; clearInterval(interval) }
  }, [])

  if (incident === undefined) return <p>Loading…</p>
  if (incident === null) return <p>No incidents yet.</p>

  return (
    <div>
      <h2>
        {incident.alert_name} — {incident.service}{' '}
        <span className={`status-badge ${incident.status}`}>{incident.status}</span>
      </h2>
      <EvidenceCard incident={incident} />
      <RootCauseCard incident={incident} />
      <SuggestionSummary suggestion={incident.pending_suggestion} />
      <Timeline entries={incident.timeline} />
    </div>
  )
}
