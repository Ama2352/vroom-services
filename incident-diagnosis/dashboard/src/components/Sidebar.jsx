import { NavLink } from 'react-router-dom'
import { useEffect, useState } from 'react'
import { Activity, ListChecks, Inbox, BookOpen } from 'lucide-react'
import { ACTORS, getActor, setActor } from '../actor.js'
import { api } from '../api.js'

const NAV_ITEMS = [
  { to: '/', end: true, label: 'Live', Icon: Activity },
  { to: '/incidents', end: false, label: 'Incidents', Icon: ListChecks },
  { to: '/pending', end: false, label: 'Pending Knowledge', Icon: Inbox, badgeKey: 'pending' },
  { to: '/knowledge', end: false, label: 'Knowledge Base', Icon: BookOpen },
]

export default function Sidebar() {
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
    <aside className="sidebar">
      <div className="sidebar-brand">Incident Agent</div>
      <nav className="sidebar-nav">
        {NAV_ITEMS.map(({ to, end, label, Icon, badgeKey }) => (
          <NavLink key={to} to={to} end={end}
                   className={({ isActive }) => `sidebar-nav-item${isActive ? ' active' : ''}`}>
            <Icon size={16} />
            {label}
            {badgeKey === 'pending' && pendingCount > 0 && (
              <span className="sidebar-nav-badge">{pendingCount}</span>
            )}
          </NavLink>
        ))}
      </nav>
      <div className="sidebar-footer">
        <div className="sidebar-footer-label">Signed in as</div>
        <select className="sidebar-actor-select" value={actor} onChange={onActorChange}>
          {ACTORS.map(a => <option key={a} value={a}>{a}</option>)}
        </select>
      </div>
    </aside>
  )
}
