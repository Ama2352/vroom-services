/**
 * EventTimeline.jsx – Vertical event timeline with filter tabs.
 */
import { useState } from 'react';
import { Activity, Truck, Bell, Layers } from 'lucide-react';
import { useDemo } from '../store/demoStore';
import './EventTimeline.css';

const FILTERS = [
  { key: 'all',          label: 'All',      icon: Layers },
  { key: 'ride',         label: 'Ride',     icon: Activity },
  { key: 'dispatch',     label: 'Dispatch', icon: Truck },
  { key: 'notification', label: 'Notif',    icon: Bell },
];

const SERVICE_COLOR = {
  ride:         '#6C63FF',
  dispatch:     '#06B6D4',
  notification: '#F59E0B',
  system:       '#4B5563',
};

const EVENT_ICON = {
  'Trip.Requested':  '📋',
  'Trip.Matched':    '🔗',
  'Trip.Accepted':   '✅',
  'Trip.PickedUp':   '🚗',
  'Trip.Completed':  '🏁',
  'Drivers.Seeded':  '🌱',
  'Demo.Reset':      '🔄',
};

function fmt(ts) {
  return ts.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export default function EventTimeline() {
  const { state } = useDemo();
  const [filter, setFilter] = useState('all');

  const events = filter === 'all'
    ? state.events
    : state.events.filter(e => e.service === filter);

  return (
    <div className="timeline-panel">
      <div className="timeline-header">
        <span className="timeline-title">Event Timeline</span>
        <span className="event-count">{state.events.length}</span>
      </div>

      {/* Filter tabs */}
      <div className="timeline-filters">
        {FILTERS.map(f => {
          const Icon = f.icon;
          return (
            <button
              key={f.key}
              id={`filter-${f.key}`}
              className={`pill-tab ${filter === f.key ? 'active' : ''}`}
              onClick={() => setFilter(f.key)}
            >
              <Icon size={11} />
              {f.label}
            </button>
          );
        })}
      </div>

      {/* Events */}
      <div className="timeline-list">
        {events.length === 0 && (
          <div className="timeline-empty">
            <Activity size={24} className="empty-icon" />
            <p>No events yet.<br />Start by seeding drivers.</p>
          </div>
        )}
        {events.map((evt, i) => (
          <div
            key={evt.id}
            className="timeline-item"
            style={{ '--line-color': SERVICE_COLOR[evt.service] ?? '#4B5563' }}
          >
            <div className="tl-dot" />
            {i < events.length - 1 && <div className="tl-line" />}
            <div className="tl-content">
              <div className="tl-top">
                <span className="tl-icon">{EVENT_ICON[evt.type] ?? '⚡'}</span>
                <span className="tl-type">{evt.type}</span>
                <span
                  className="tl-service"
                  style={{ color: SERVICE_COLOR[evt.service] ?? '#4B5563' }}
                >
                  {evt.service}
                </span>
              </div>
              <div className="tl-bottom">
                <span className="tl-time">{fmt(evt.ts)}</span>
                {evt.detail?.tripId && (
                  <span className="tl-trip mono">#{evt.detail.tripId.toString().slice(-6)}</span>
                )}
                {evt.detail?.offline && (
                  <span className="tl-offline">offline</span>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
