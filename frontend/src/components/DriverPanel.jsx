/**
 * DriverPanel.jsx – Driver mock app (right column, top half).
 */
import { Car, MapPin, CheckCircle } from 'lucide-react';
import { useDemo, TRIP_STATUS } from '../store/demoStore';
import './DriverPanel.css';

export default function DriverPanel() {
  const { state, actions } = useDemo();
  const { drivers, assignedDriver, tripStatus, notifications, tripId } = state;

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
        <div className={`driver-status-badge ${isActive ? 'active' : ''} ${tripStatus === TRIP_STATUS.CANCELLED ? 'cancelled' : ''}`}>
          <span className={isActive ? (tripStatus === TRIP_STATUS.CANCELLED ? 'dot-red' : 'dot-yellow') : 'dot-muted'} />
          {tripStatus === TRIP_STATUS.CANCELLED ? 'Cancelled' : (isActive ? 'On Duty' : 'Standby')}
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

            {/* Driver Actions */}
            <div className="driver-actions mt-3">
              {tripStatus === TRIP_STATUS.ASSIGNED && (
                <div className="flex gap-2">
                  <button 
                    className="btn-success flex-1"
                    onClick={() => actions.acceptTrip(tripId)}
                  >
                    Accept Trip
                  </button>
                  <button 
                    className="btn-danger-outline"
                    onClick={() => actions.rejectOffer(tripId, assignedDriver.id)}
                  >
                    Reject
                  </button>
                </div>
              )}

              {tripStatus === TRIP_STATUS.ACCEPTED && (
                <div className="flex gap-2">
                  <button 
                    className="btn-primary flex-1"
                    disabled={state.driverMoving}
                    onClick={() => actions.startTrip(state.tripId)}
                  >
                    Start Trip
                  </button>
                  <button 
                    className="btn-danger-outline"
                    onClick={() => actions.cancelTrip(tripId, 'Driver cancelled')}
                  >
                    Cancel
                  </button>
                </div>
              )}

              {tripStatus === TRIP_STATUS.ON_TRIP && (
                <div className="flex gap-2">
                  <button 
                    className="btn-success flex-1"
                    disabled={state.driverMoving}
                    onClick={() => actions.completeTrip(tripId)}
                  >
                    Complete Trip
                  </button>
                </div>
              )}
            </div>
            {(tripStatus === TRIP_STATUS.COMPLETED || tripStatus === TRIP_STATUS.CANCELLED) && (
              <button className="driver-reset-btn" onClick={() => window.location.reload()}>
                Ready for Next Trip
              </button>
            )}
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
