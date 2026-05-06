/**
 * SystemMonitor.jsx – Slide-in system panel (Online Drivers + Event Timeline).
 * Toggled via TopBar button; displays system-level info outside the driver UX.
 */
import { useState } from 'react';
import { X, Users, Activity, Truck, Bell, Layers, CheckCircle } from 'lucide-react';
import { useDemo } from '../store/demoStore';
import './SystemMonitor.css';

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

export default function SystemMonitor({ open, onClose }) {
  const { state } = useDemo();
  const { drivers, assignedDriver, events } = state;
  const [filter, setFilter] = useState('all');

  const filteredEvents = filter === 'all'
    ? events
    : events.filter(e => e.service === filter);

  return (
    <>
      {/* Backdrop */}
      {open && <div className="sysmon-backdrop" onClick={onClose} />}

      {/* Slide-in panel */}
      <div className={`sysmon-panel ${open ? 'sysmon-open' : ''}`}>
        {/* Panel header */}
        <div className="sysmon-header">
          <div className="sysmon-title-group">
            <div className="sysmon-icon-wrap">
              <Activity size={14} />
            </div>
            <span className="sysmon-title">System Monitor</span>
          </div>
          <button className="sysmon-close" onClick={onClose}>
            <X size={16} />
          </button>
        </div>

        <div className="sysmon-body">
          {/* ── Online Drivers Section ── */}
          <div className="sysmon-section">
            <div className="sysmon-section-header">
              <Users size={12} />
              <span>Online Drivers</span>
              <span className="sysmon-count">{drivers.length}</span>
            </div>

            <div className="sysmon-driver-list">
              {drivers.length === 0 && (
                <div className="sysmon-empty">Seed drivers to see them here</div>
              )}
              {drivers.map(d => {
                const isAssigned = assignedDriver?.id === d.id;
                return (
                  <div key={d.id} className={`sysmon-driver-item ${isAssigned ? 'assigned' : ''}`}>
                    <div className="sysmon-driver-avatar">{d.avatar}</div>
                    <div className="sysmon-driver-info">
                      <div className="sysmon-driver-name">{d.name}</div>
                      <div className="sysmon-driver-coord mono">
                        {d.lat.toFixed(4)}, {d.lng.toFixed(4)}
                      </div>
                    </div>
                    {isAssigned && (
                      <div className="sysmon-assigned-tag">
                        <CheckCircle size={11} />
                        Active
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          <div className="sysmon-divider" />

          {/* ── Event Timeline Section ── */}
          <div className="sysmon-section sysmon-timeline-section">
            <div className="sysmon-section-header">
              <Activity size={12} />
              <span>Event Timeline</span>
              <span className="sysmon-count">{events.length}</span>
            </div>

            {/* Filter tabs */}
            <div className="sysmon-filters">
              {FILTERS.map(f => {
                const Icon = f.icon;
                return (
                  <button
                    key={f.key}
                    id={`sysmon-filter-${f.key}`}
                    className={`pill-tab ${filter === f.key ? 'active' : ''}`}
                    onClick={() => setFilter(f.key)}
                  >
                    <Icon size={10} />
                    {f.label}
                  </button>
                );
              })}
            </div>

            {/* Events list */}
            <div className="sysmon-event-list">
              {filteredEvents.length === 0 && (
                <div className="sysmon-empty">
                  <Activity size={20} className="sysmon-empty-icon" />
                  <p>No events yet.<br />Start by seeding drivers.</p>
                </div>
              )}
              {filteredEvents.map((evt, i) => (
                <div
                  key={evt.id}
                  className="sysmon-event-item"
                  style={{ '--line-color': SERVICE_COLOR[evt.service] ?? '#4B5563' }}
                >
                  <div className="sysmon-tl-dot" />
                  {i < filteredEvents.length - 1 && <div className="sysmon-tl-line" />}
                  <div className="sysmon-tl-content">
                    <div className="sysmon-tl-top">
                      <span className="sysmon-tl-icon">{EVENT_ICON[evt.type] ?? '⚡'}</span>
                      <span className="sysmon-tl-type">{evt.type}</span>
                      <span
                        className="sysmon-tl-service"
                        style={{ color: SERVICE_COLOR[evt.service] ?? '#4B5563' }}
                      >
                        {evt.service}
                      </span>
                    </div>
                    <div className="sysmon-tl-bottom">
                      <span className="sysmon-tl-time">{fmt(evt.ts)}</span>
                      {evt.detail?.tripId && (
                        <span className="sysmon-tl-trip mono">#{evt.detail.tripId.toString().slice(-6)}</span>
                      )}
                      {evt.detail?.offline && (
                        <span className="sysmon-tl-offline">offline</span>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
