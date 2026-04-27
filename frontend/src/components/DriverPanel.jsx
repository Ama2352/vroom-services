/**
 * DriverPanel.jsx – Driver mock app (right column, top half).
 */
import { Car, MapPin, CheckCircle } from 'lucide-react';
import { useDemo, TRIP_STATUS } from '../store/demoStore';
import './DriverPanel.css';

export default function DriverPanel() {
  const { state } = useDemo();
  const { drivers, assignedDriver, tripStatus, notifications } = state;

  const driverNotifs = notifications
    .filter(n => n.side === 'driver')
    .slice(0, 4);

  const isActive = tripStatus !== TRIP_STATUS.IDLE;

  return (
    <div className="driver-panel">
      <div className="panel-header">
        <div className="panel-avatar driver-avatar">
          <Car size={16} />
        </div>
        <div>
          <div className="panel-title">Driver App</div>
          <div className="panel-sub">
            {assignedDriver ? assignedDriver.id : 'No driver assigned'}
          </div>
        </div>
        <div className={`driver-status-badge ${isActive ? 'active' : ''}`}>
          <span className={isActive ? 'dot-yellow' : 'dot-muted'} />
          {isActive ? 'On Duty' : 'Standby'}
        </div>
      </div>

      <div className="divider" />

      {/* Online drivers list */}
      <div className="driver-list-section">
        <div className="section-title">Online Drivers ({drivers.length})</div>
        <div className="driver-list">
          {drivers.length === 0 && (
            <div className="driver-empty">Seed drivers to see them here</div>
          )}
          {drivers.map(d => {
            const isAssigned = assignedDriver?.id === d.id;
            return (
              <div key={d.id} className={`driver-item ${isAssigned ? 'assigned' : ''}`}>
                <div className="driver-avatar-sm">{d.avatar}</div>
                <div className="driver-info">
                  <div className="driver-name">{d.name}</div>
                  <div className="driver-coord mono">
                    {d.lat.toFixed(4)}, {d.lng.toFixed(4)}
                  </div>
                </div>
                {isAssigned && (
                  <div className="assigned-tag">
                    <CheckCircle size={12} />
                    Active
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Current trip info */}
      {assignedDriver && (
        <>
          <div className="divider" />
          <div className="current-trip-section">
            <div className="section-title">Current Trip</div>
            <div className="trip-info-card">
              <div className="trip-info-row">
                <MapPin size={12} className="ti-icon pickup" />
                <span className="ti-label">From</span>
                <span className="ti-value">{state.pickup.label}</span>
              </div>
              <div className="trip-connector-line" />
              <div className="trip-info-row">
                <MapPin size={12} className="ti-icon dropoff" />
                <span className="ti-label">To</span>
                <span className="ti-value">{state.dropoff.label}</span>
              </div>
            </div>
          </div>
        </>
      )}

      <div className="divider" />

      {/* Driver notifications */}
      <div className="driver-notif-section">
        <div className="section-title">Notifications</div>
        <div className="notif-list">
          {driverNotifs.length === 0 && (
            <div className="notif-empty">No driver notifications</div>
          )}
          {driverNotifs.map(n => (
            <div key={n.id} className={`notif-item notif-${n.variant}`}>
              <span className="notif-dot" />
              <div>
                <div className="notif-msg">{n.message}</div>
                <div className="notif-time">{n.ts.toLocaleTimeString('vi-VN')}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
