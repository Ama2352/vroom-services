import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import EvidenceCard from '../components/EvidenceCard.jsx'
import RootCauseCard from '../components/RootCauseCard.jsx'
import SuggestionSummary from '../components/SuggestionSummary.jsx'
import Timeline from '../components/Timeline.jsx'
import { api } from '../api.js'
import { getActor } from '../actor.js'

export default function IncidentDetailPage() {
  const { id } = useParams()
  const [incident, setIncident] = useState(undefined)

  function load() {
    api.get(`/incidents/${id}`).then(r => setIncident(r.data.incident))
  }

  useEffect(load, [id])

  function resolve() {
    api.post(`/incidents/${id}/resolve`, { actor: getActor() }).then(load)
  }

  if (incident === undefined) return <p>Loading…</p>

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
      {incident.status === 'open' && (
        <button className="button" onClick={resolve}>Resolve Incident</button>
      )}
    </div>
  )
}
