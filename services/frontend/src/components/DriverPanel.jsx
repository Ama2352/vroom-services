import { Car, MapPin, CheckCircle } from 'lucide-react';
import { useDemo, TRIP_STATUS } from '../store/demoStore';
import './DriverPanel.css';

export default function DriverPanel() {
  const { state, actions } = useDemo();
  const {
    assignedDriver, tripStatus, notifications,
    tripId, driverMoving, pickup, dropoff,
    sessionTrips, sessionEarnings, estimatedFare, tripDetails,
  } = state;

  const isAssigned  = tripStatus === TRIP_STATUS.ASSIGNED;
  const isAccepted  = tripStatus === TRIP_STATUS.ACCEPTED || tripStatus === TRIP_STATUS.COMING;
  const isOnTrip    = tripStatus === TRIP_STATUS.ON_TRIP;
  const isCompleted = tripStatus === TRIP_STATUS.COMPLETED;
  const isCancelled = tripStatus === TRIP_STATUS.CANCELLED;
  const isActive    = tripStatus !== TRIP_STATUS.IDLE && !isCompleted && !isCancelled;

  const driverNotifs = notifications.filter(n => n.side === 'driver').slice(0, 5);

  const finalFare = tripDetails?.final_price?.amount ?? estimatedFare;

  return (
    <div className="dp-panel">

      {/* ── Section 1: Header ── */}
      <div className="dp-header">
        <div className="dp-avatar">
          {assignedDriver ? assignedDriver.avatar : <Car size={16} color="var(--cyan)" />}
        </div>
        <div>
          <div className="dp-role-label">
            {assignedDriver ? assignedDriver.name : 'Driver App'}
          </div>
          <div className="dp-role-sub">driver-perspective</div>
        </div>
        <div className="dp-session-stats">
          <span className="stat-chip">{sessionTrips} trip{sessionTrips !== 1 ? 's' : ''}</span>
          {sessionEarnings > 0 && (
            <span className="stat-chip stat-chip-green">
              {sessionEarnings.toLocaleString('vi-VN')} VND
            </span>
          )}
          <div className={`dp-status-badge ${isActive ? 'active' : ''} ${isCancelled ? 'cancelled' : ''}`}>
            <span className={isCancelled ? 'dot-red' : isActive ? 'dot-yellow' : 'dot-muted'} />
            {isCancelled ? 'Cancelled' : isActive ? 'On Duty' : 'Standby'}
          </div>
        </div>
      </div>

      {/* ── Standby state ── */}
      {!assignedDriver && !isCompleted && !isCancelled && (
        <div className="dp-standby">
          <div className="dp-standby-icon">🚗</div>
          <div className="dp-standby-title">Waiting for ride requests</div>
          <div className="dp-standby-sub">
            {tripStatus === TRIP_STATUS.IDLE
              ? 'Seed drivers and request a ride to start the demo'
              : 'Matching in progress...'}
          </div>
        </div>
      )}

      {/* ── Section 2: Incoming Offer (ASSIGNED only) ── */}
      {isAssigned && assignedDriver && (
        <div className="dp-offer-card">
          <div className="dp-offer-header">
            <span className="dp-offer-title">New Ride Offer</span>
            <span className="dp-offer-fare-badge">{estimatedFare.toLocaleString('vi-VN')} VND</span>
          </div>

          <div className="dp-route-card">
            <div className="dp-route-row">
              <MapPin size={11} className="dp-route-icon-pickup" />
              <span className="dp-route-label">From</span>
              <span className="dp-route-value">{pickup.label}</span>
            </div>
            <div className="dp-route-divider" />
            <div className="dp-route-row">
              <MapPin size={11} className="dp-route-icon-dropoff" />
              <span className="dp-route-label">To</span>
              <span className="dp-route-value">{dropoff.label}</span>
            </div>
          </div>

          <div className="dp-offer-meta">
            <span>⏱ ~{state.estimatedTime} min</span>
            <span>·</span>
            <span>📍 {pickup.label}</span>
          </div>

          <div className="dp-offer-actions">
            <button
              className="btn-success flex-1"
              onClick={() => actions.acceptTrip(tripId, assignedDriver.id)}
            >
              Accept Trip
            </button>
            <button
              className="btn-danger-outline flex-1"
              onClick={() => actions.rejectOffer(tripId, assignedDriver.id)}
            >
              Reject
            </button>
          </div>
        </div>
      )}

      {/* ── Section 3: Active trip (ACCEPTED / ON_TRIP) ── */}
      {(isAccepted || isOnTrip) && assignedDriver && (
        <div className="dp-active-card">
          <div className="dp-active-status">
            <div className={`dp-active-dot ${isAccepted ? 'dp-active-dot-cyan' : 'dp-active-dot-brand'}`} />
            <span className="dp-active-label">
              {isAccepted ? 'En route to pickup' : 'Taking passenger to destination'}
            </span>
          </div>

          <div className="dp-route-card">
            <div className="dp-route-row">
              <MapPin size={11} className="dp-route-icon-pickup" />
              <span className="dp-route-label">From</span>
              <span className="dp-route-value">{pickup.label}</span>
            </div>
            <div className="dp-route-divider" />
            <div className="dp-route-row">
              <MapPin size={11} className="dp-route-icon-dropoff" />
              <span className="dp-route-label">To</span>
              <span className="dp-route-value">{dropoff.label}</span>
            </div>
          </div>

          <div className="dp-active-actions">
            {isAccepted && (
              <>
                <button
                  className="btn-primary flex-1"
                  disabled={driverMoving}
                  onClick={() => actions.startTrip(tripId)}
                >
                  {driverMoving ? 'Driving to pickup…' : 'Start Trip'}
                </button>
                <button
                  className="btn-danger-outline"
                  onClick={() => actions.cancelTrip(tripId, 'Driver cancelled')}
                >
                  Cancel
                </button>
              </>
            )}
            {isOnTrip && (
              <button
                className="btn-success w-full"
                disabled={driverMoving}
                onClick={() => actions.completeTrip(tripId)}
              >
                {driverMoving ? 'Driving to destination…' : 'Complete Trip'}
              </button>
            )}
          </div>
        </div>
      )}

      {/* ── Section 4a: Trip complete summary ── */}
      {isCompleted && (
        <div className="dp-complete-card">
          <div className="dp-complete-title">
            <CheckCircle size={16} />
            Trip Complete!
          </div>
          <div className="dp-complete-stats">
            <div className="dp-complete-stat">
              <div className="dp-complete-stat-label">Final Fare</div>
              <div className="dp-complete-stat-value dp-complete-stat-value-green">
                {finalFare.toLocaleString('vi-VN')} VND
              </div>
            </div>
            <div className="dp-complete-stat">
              <div className="dp-complete-stat-label">Duration</div>
              <div className="dp-complete-stat-value">~{state.estimatedTime} min</div>
            </div>
            <div className="dp-complete-stat">
              <div className="dp-complete-stat-label">Session</div>
              <div className="dp-complete-stat-value">{sessionTrips} trip{sessionTrips !== 1 ? 's' : ''}</div>
            </div>
          </div>
          <button className="btn-primary w-full" onClick={actions.reset}>
            Ready for Next Trip
          </button>
        </div>
      )}

      {/* ── Section 4b: Cancelled ── */}
      {isCancelled && (
        <>
          <div className="dp-cancelled-card">
            <span>❌</span>
            Trip was cancelled
          </div>
          <div style={{ padding: '0 14px 12px' }}>
            <button className="btn-primary w-full" onClick={actions.reset}>
              Ready for Next Trip
            </button>
          </div>
        </>
      )}

      {/* ── Section 5: Notifications ── */}
      <div className="dp-notif-section">
        <div className="dp-notif-title">Driver Notifications</div>
        <div className="dp-notif-list">
          {driverNotifs.length === 0
            ? <div className="dp-notif-empty">No notifications yet</div>
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
