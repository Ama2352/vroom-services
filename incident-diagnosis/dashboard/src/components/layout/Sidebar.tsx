import { NavLink } from 'react-router-dom'
import { useEffect, useState, type ChangeEvent } from 'react'
import { Activity, ListChecks, Inbox, BookOpen, History } from 'lucide-react'
import { ACTORS, getActor, setActor } from '../../lib/actor'
import { api } from '../../lib/api'
import { cn } from '../../lib/cn'

const NAV_ITEMS = [
  { to: '/', end: true, label: 'Live', Icon: Activity, badgeKey: undefined },
  { to: '/incidents', end: false, label: 'Incidents', Icon: ListChecks, badgeKey: undefined },
  { to: '/pending', end: false, label: 'Pending Knowledge', Icon: Inbox, badgeKey: 'pending' as const },
  { to: '/knowledge', end: false, label: 'Knowledge Base', Icon: BookOpen, badgeKey: undefined },
  { to: '/history', end: false, label: 'History', Icon: History, badgeKey: undefined },
]

export function Sidebar() {
  const [actor, setActorState] = useState(getActor())
  const [pendingCount, setPendingCount] = useState(0)

  useEffect(() => {
    api.get('/pending', { params: { status: 'pending' } })
      .then(r => setPendingCount(r.data.pending.length))
      .catch(() => {})
  }, [])

  function onActorChange(e: ChangeEvent<HTMLSelectElement>) {
    setActor(e.target.value)
    setActorState(e.target.value)
  }

  return (
    <aside className="sticky top-0 flex h-screen w-[200px] shrink-0 flex-col border-r border-border bg-surface max-[720px]:static max-[720px]:h-auto max-[720px]:w-full max-[720px]:flex-row max-[720px]:items-center max-[720px]:overflow-x-auto max-[720px]:border-b max-[720px]:border-r-0">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3.5 text-sm font-bold text-ink max-[720px]:hidden">
        <span className="h-2 w-2 rounded-sm bg-accent" />
        Incident Agent
      </div>
      <nav className="flex flex-1 flex-col gap-0.5 p-2 max-[720px]:flex-none max-[720px]:flex-row">
        {NAV_ITEMS.map(({ to, end, label, Icon, badgeKey }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-2 whitespace-nowrap rounded-md px-2.5 py-2 text-[13px] font-medium text-ink-soft hover:bg-white/5',
                isActive && 'bg-accent-soft text-accent hover:bg-accent-soft',
              )
            }
          >
            <Icon size={16} />
            {label}
            {badgeKey === 'pending' && pendingCount > 0 && (
              <span className="ml-auto rounded-full bg-critical px-1.5 text-[11px] font-semibold leading-[1.5] text-white">
                {pendingCount}
              </span>
            )}
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-border p-2.5 max-[720px]:flex max-[720px]:shrink-0 max-[720px]:items-center max-[720px]:gap-2 max-[720px]:border-t-0 max-[720px]:border-l max-[720px]:p-2">
        <div className="mb-1 text-[11px] text-ink-faint max-[720px]:hidden">Signed in as</div>
        <select
          className="w-full rounded-md border border-border bg-surface px-2 py-1.5 text-[12.5px] text-ink max-[720px]:w-auto"
          value={actor}
          onChange={onActorChange}
        >
          {ACTORS.map(a => <option key={a} value={a}>{a}</option>)}
        </select>
      </div>
    </aside>
  )
}
