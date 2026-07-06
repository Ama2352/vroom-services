import { NavLink } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { ACTORS, getActor, setActor } from '../actor.js'
import { api } from '../api.js'

export default function NavBar() {
  const [actor, setActorState] = useState(getActor())
  const [pendingCount, setPendingCount] = useState(0)

  useEffect(() => {
    api.get('/pending', { params: { status: 'pending' } })
      .then(r => setPendingCount(r.data.pending.length))
      .catch(() => {})
  }, [])

  function onActorChange(e) {
    setActor(e.target.value)
    setActorState(e.target.value)
  }

  return (
    <nav className="navbar">
      <NavLink to="/" end>Live</NavLink>
      <NavLink to="/incidents">Incidents</NavLink>
      <NavLink to="/pending">
        Pending Knowledge{pendingCount > 0 && <span className="badge">{pendingCount}</span>}
      </NavLink>
      <NavLink to="/knowledge">Knowledge Base</NavLink>
      <select className="actor-select" value={actor} onChange={onActorChange}>
        {ACTORS.map(a => <option key={a} value={a}>{a}</option>)}
      </select>
    </nav>
  )
}
