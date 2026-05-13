import { Car, MapPin } from 'lucide-react';
import { useDemo, TRIP_STATUS } from '../store/demoStore';
import './DriverPanel.css';

export default function DriverPanel() {
  const { state, actions } = useDemo();
  const {
    drivers, assignedDriver, tripStatus,
    notifications, tripId, driverMoving, pickup, dropoff,
  } = state;

  const driverNotifs = notifications
    .filter(n => n.side === 'driver')
    .slice(0, 4);

  const isActive    = tripStatus !== TRIP_STATUS.IDLE;
  const isCancelled = tripStatus === TRIP_STATUS.CANCELLED;
  const isCompleted = tripStatus === TRIP_STATUS.COMPLETED;

  return (
    <div className="driver-panel">

      {/* Header */}
      <div className="panel-header">
        <div className="panel-avatar driver-avatar"><Car size={16} /></div>
        <div>
          <div className="panel-title">Driver App</div>
          <div className="panel-sub">
            {assignedDriver ? assignedDriver.name : 'No driver assigned'}
          </div>
        </div>
        <div className={`driver-status-badge ${isActive ? 'active' : ''} ${isCancelled ? 'cancelled' : ''}`}>
          <span className={
            isCancelled ? 'dot-red' : isActive ? 'dot-yellow' : 'dot-muted'
          } />
          {isCancelled ? 'Cancelled' : isActive ? 'On Duty' : 'Standby'}
        </div>
      </div>

      <div className="divider" />

      {/* Active trip panel */}
      {assignedDriver && !isCompleted && !isCancelled && (
        <>
          <div className="current-trip-section">
            <div className="section-title">Current Trip</div>
            <div className="trip-info-card">
              <div className="trip-info-row">
                <MapPin size={12} className="ti-icon pickup" />
                <span className="ti-label">From</span>
                <span className="ti-value">{pickup.label}</span>
              </div>
              <div className="trip-connector-line" />
              <div className="trip-info-row">
                <MapPin size={12} className="ti-icon dropoff" />
                <span className="ti-label">To</span>
                <span className="ti-value">{dropoff.label}</span>
              </div>
            </div>

            {/* Driver action buttons */}
            <div className="driver-actions mt-3">

              {/* ASSIGNED → driver can accept or reject */}
              {tripStatus === TRIP_STATUS.ASSIGNED && (
                <div className="flex gap-2">
                  <button
                    className="btn-success flex-1"
                    onClick={() => actions.acceptTrip(tripId, assignedDriver.id)}
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

              {/* ACCEPTED → driver moving to pickup; start trip once arrived */}
              {tripStatus === TRIP_STATUS.ACCEPTED && (
                <div className="flex gap-2">
                  <button
                    className="btn-primary flex-1"
                    disabled={driverMoving}
                    onClick={() => actions.startTrip(tripId)}
                  >
                    {driverMoving ? 'En route to pickup…' : 'Start Trip'}
                  </button>
                  <button
                    className="btn-danger-outline"
                    onClick={() => actions.cancelTrip(tripId, 'Driver cancelled')}
                  >
                    Cancel
                  </button>
                </div>
              )}

              {/* ON_TRIP → driver moving to destination; complete when arrived */}
              {tripStatus === TRIP_STATUS.ON_TRIP && (
                <button
                  className="btn-success w-full"
                  disabled={driverMoving}
                  onClick={() => actions.completeTrip(tripId)}
                >
                  {driverMoving ? 'En route to destination…' : 'Complete Trip'}
                </button>
              )}

            </div>
          </div>
          <div className="divider" />
        </>
      )}

      {/* Terminal state — ready for next trip */}
      {(isCompleted || isCancelled) && (
        <>
          <div className="current-trip-section">
            <button className="driver-reset-btn" onClick={actions.reset}>
              Ready for Next Trip
            </button>
          </div>
          <div className="divider" />
        </>
      )}

      {/* Driver notifications */}
      <div className="driver-notif-section">
        <div className="section-title">Notifications</div>
        <div className="notif-list">
          {driverNotifs.length === 0
            ? <div className="notif-empty">No driver notifications</div>
            : driverNotifs.map(n => (
              <div key={n.id} className={`notif-item notif-${n.variant}`}>
                <span className="notif-dot" />
                <div>
                  <div className="notif-msg">{n.message}</div>
                  <div className="notif-time">{n.ts.toLocaleTimeString('vi-VN')}</div>
                </div>
              </div>
            ))
          }
        </div>
      </div>

    </div>
  );
}
