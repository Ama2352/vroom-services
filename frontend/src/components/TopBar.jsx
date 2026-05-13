import { useEffect, useState } from 'react';
import { Zap, Activity } from 'lucide-react';
import { useDemo, TRIP_STATUS } from '../store/demoStore';
import './TopBar.css';

const STATUS_BADGE = {
  [TRIP_STATUS.IDLE]:      'badge-idle',
  [TRIP_STATUS.SEARCHING]: 'badge-searching',
  [TRIP_STATUS.ASSIGNED]:  'badge-assigned',
  [TRIP_STATUS.COMING]:    'badge-coming',
  [TRIP_STATUS.ON_TRIP]:   'badge-ontrip',
  [TRIP_STATUS.COMPLETED]: 'badge-completed',
};

const STATUS_DOT = {
  [TRIP_STATUS.IDLE]:      '#4B5563',
  [TRIP_STATUS.SEARCHING]: '#F59E0B',
  [TRIP_STATUS.ASSIGNED]:  '#3B82F6',
  [TRIP_STATUS.COMING]:    '#06B6D4',
  [TRIP_STATUS.ON_TRIP]:   '#6C63FF',
  [TRIP_STATUS.COMPLETED]: '#22C55E',
};

const WS_COLOR = {
  connected:    '#22C55E',
  disconnected: '#EF4444',
  connecting:   '#F59E0B',
};

export default function TopBar({ onToggleMonitor, monitorOpen }) {
  const { state } = useDemo();
  const [time, setTime] = useState(new Date());

  useEffect(() => {
    const t = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const badgeClass = STATUS_BADGE[state.tripStatus] ?? 'badge-idle';
  const dotColor   = STATUS_DOT[state.tripStatus]   ?? '#4B5563';
  const wsColor    = WS_COLOR[state.wsStatus]        ?? '#F59E0B';
  const wsLabel    = state.wsStatus === 'connected'
    ? 'LIVE'
    : state.wsStatus === 'disconnected'
    ? 'OFFLINE'
    : 'CONNECTING';

  return (
    <header className="topbar">
      <div className="topbar-brand">
        <div className="topbar-logo">
          <Zap size={18} fill="#6C63FF" color="#6C63FF" />
        </div>
        <span className="topbar-name">Vroom</span>
        <span className="topbar-sub">Ride Hailing Demo</span>
      </div>

      <div className="topbar-center">
        <div className={`badge ${badgeClass}`}>
          <span className="status-dot" style={{ background: dotColor }} />
          {state.tripStatus}
        </div>
        {state.tripId && (
          <span className="topbar-trip-id mono">
            #{state.tripId.toString().slice(-8)}
          </span>
        )}
      </div>

      <div className="topbar-right">
        <span className="topbar-time mono">
          {time.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
        </span>
        <div className="topbar-dot-row" title={`Notification WS: ${state.wsStatus}`}>
          <span className="live-dot" style={{ background: wsColor }} />
          <span className="text-sm text-muted">{wsLabel}</span>
        </div>
        <button
          id="btn-toggle-system-monitor"
          className={`topbar-monitor-btn ${monitorOpen ? 'active' : ''}`}
          onClick={onToggleMonitor}
          title="System Monitor"
        >
          <Activity size={14} />
          <span>Monitor</span>
          {state.drivers.length > 0 && (
            <span className="topbar-monitor-badge">{state.drivers.length}</span>
          )}
        </button>
      </div>
    </header>
  );
}
