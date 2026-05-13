import { MapPin, Navigation, User, Clock, CreditCard, CheckCircle } from 'lucide-react';
import { useDemo, PICKUP_PRESETS, DROPOFF_PRESETS, TRIP_STATUS } from '../store/demoStore';
import './PassengerPanel.css';

/* Mini strip step config */
const STRIP_STEPS = [
  { label: 'Request',   statuses: [TRIP_STATUS.IDLE] },
  { label: 'Searching', statuses: [TRIP_STATUS.SEARCHING] },
  { label: 'Matched',   statuses: [TRIP_STATUS.ASSIGNED] },
  { label: 'Accepted',  statuses: [TRIP_STATUS.ACCEPTED, TRIP_STATUS.COMING] },
  { label: 'On Trip',   statuses: [TRIP_STATUS.ON_TRIP] },
  { label: 'Done',      statuses: [TRIP_STATUS.COMPLETED] },
];

function getStripIndex(status) {
  const i = STRIP_STEPS.findIndex(s => s.statuses.includes(status));
  return i === -1 ? 0 : i;
}

export default function PassengerPanel() {
  const { state, actions } = useDemo();
  const {
    tripStatus, pickup, dropoff, notifications,
    tripId, estimatedFare, estimatedTime,
    error, assignedDriver, drivers, tripDetails,
  } = state;

  const isIdle      = tripStatus === TRIP_STATUS.IDLE;
  const isSearching = tripStatus === TRIP_STATUS.SEARCHING;
  const isAssigned  = tripStatus === TRIP_STATUS.ASSIGNED;
  const isAccepted  = tripStatus === TRIP_STATUS.ACCEPTED || tripStatus === TRIP_STATUS.COMING;
  const isOnTrip    = tripStatus === TRIP_STATUS.ON_TRIP;
  const isCompleted = tripStatus === TRIP_STATUS.COMPLETED;
  const isCancelled = tripStatus === TRIP_STATUS.CANCELLED;
  const canCancel   = isSearching || isAssigned || isAccepted;

  const passengerNotifs = notifications.filter(n => n.side === 'passenger').slice(0, 5);
  const currentIdx = getStripIndex(tripStatus);

  // Prefer actual final price from DB if available
  const displayFare = tripDetails?.final_price?.amount
    ? tripDetails.final_price.amount
    : estimatedFare;

  return (
    <div className="pp-panel">

      {/* ── Section 1: Header ── */}
      <div className="pp-header">
        <div className="pp-avatar">
          <User size={16} />
        </div>
        <div>
          <div className="pp-role-label">Passenger</div>
          <div className="pp-user-chip">passenger-demo</div>
        </div>
        <div className="pp-online-badge">
          <span className="dot-green" />
          Online
        </div>
      </div>

      {/* ── Section 2: Booking ── */}
      <div className="pp-section">
        {/* Route */}
        <div className="pp-loc-field">
          <div className="pp-loc-icon pp-loc-icon-pickup">
            <MapPin size={12} />
          </div>
          <div className="pp-loc-inner">
            <div className="pp-loc-label">Pickup</div>
            <select
              className="pp-loc-select"
              value={pickup.label}
              disabled={!isIdle}
              onChange={e => {
                const p = PICKUP_PRESETS.find(x => x.label === e.target.value);
                if (p) actions.setPickup(p);
              }}
            >
              {PICKUP_PRESETS.map(p => (
                <option key={p.label} value={p.label}>{p.label}</option>
              ))}
            </select>
          </div>
        </div>

        <div className="pp-route-connector">
          <div className="pp-connector-line" />
        </div>

        <div className="pp-loc-field">
          <div className="pp-loc-icon pp-loc-icon-dropoff">
            <Navigation size={12} />
          </div>
          <div className="pp-loc-inner">
            <div className="pp-loc-label">Destination</div>
            <select
              className="pp-loc-select"
              value={dropoff.label}
              disabled={!isIdle}
              onChange={e => {
                const d = DROPOFF_PRESETS.find(x => x.label === e.target.value);
                if (d) actions.setDropoff(d);
              }}
            >
              {DROPOFF_PRESETS.map(d => (
                <option key={d.label} value={d.label}>{d.label}</option>
              ))}
            </select>
          </div>
        </div>

        {/* Fare estimate */}
        <div className="pp-fare-card">
          <div className="pp-fare-item">
            <div className="pp-fare-label"><Clock size={11} /> Time</div>
            <div className="pp-fare-value">~{estimatedTime} min</div>
          </div>
          <div className="pp-fare-item">
            <div className="pp-fare-label"><CreditCard size={11} /> Fare</div>
            <div className={`pp-fare-value ${!isIdle ? 'pp-fare-value-green' : ''}`}>
              {displayFare.toLocaleString('vi-VN')} VND
            </div>
          </div>
        </div>
      </div>

      {/* ── Section 3: Status strip ── */}
      <div className="pp-status-strip">
        {STRIP_STEPS.map((step, i) => {
          const phase = isCancelled ? 'future' : i < currentIdx ? 'done' : i === currentIdx ? 'active' : 'future';
          return (
            <div key={step.label} style={{ display: 'flex', alignItems: 'center', flex: i < STRIP_STEPS.length - 1 ? '1' : '0' }}>
              <div className={`pp-strip-dot pp-strip-dot-${phase}`} />
              {i < STRIP_STEPS.length - 1 && (
                <div className={`pp-strip-line ${i < currentIdx && !isCancelled ? 'pp-strip-line-filled' : 'pp-strip-line-empty'}`} />
              )}
            </div>
          );
        })}
        <span className={`pp-strip-label ${isCancelled ? 'pp-strip-label-cancelled' : ''}`}>
          {isCancelled ? 'Cancelled' : STRIP_STEPS[currentIdx]?.label}
        </span>
      </div>

      {/* ── Section 4: Driver card (conditional) ── */}
      {(isAssigned || isAccepted) && assignedDriver && (
        <div className="pp-driver-card">
          <div className="pp-driver-row">
            <span className="pp-driver-emoji">{assignedDriver.avatar}</span>
            <div>
              <div className="pp-driver-name">{assignedDriver.name}</div>
              <div className="pp-driver-sub">Your assigned driver</div>
            </div>
            <div className={`pp-driver-eta ${isAccepted ? 'pp-driver-eta-onway' : 'pp-driver-eta-arriving'}`}>
              {isAccepted ? 'On the way' : 'Arriving...'}
            </div>
          </div>
        </div>
      )}

      {/* Trip complete summary */}
      {isCompleted && (
        <div className="pp-complete-card">
          <div className="pp-complete-title">
            <CheckCircle size={16} />
            Trip Complete!
          </div>
          <div className="pp-complete-stats">
            <div className="pp-complete-stat">
              <div className="pp-complete-stat-label">Final Fare</div>
              <div className="pp-complete-stat-value pp-complete-stat-value-green">
                {displayFare.toLocaleString('vi-VN')} VND
              </div>
            </div>
            <div className="pp-complete-stat">
              <div className="pp-complete-stat-label">Duration</div>
              <div className="pp-complete-stat-value">~{estimatedTime} min</div>
            </div>
          </div>
        </div>
      )}

      {/* Cancelled card */}
      {isCancelled && (
        <div className="pp-cancelled-card">
          <span>❌</span>
          Trip was cancelled
        </div>
      )}

      {/* ── Section 5: Actions ── */}
      <div className="pp-actions">
        {/* Error banner */}
        {error && (
          <div
            className="notif-item notif-warning"
            style={{ cursor: 'pointer', marginBottom: 8 }}
            onClick={actions.dismissError}
          >
            <span className="notif-dot" />
            <div>
              <div className="notif-msg">{error}</div>
              <div className="notif-time">Tap to dismiss</div>
            </div>
          </div>
        )}

        {isIdle && (
          <button
            className="btn-primary w-full"
            disabled={drivers.length === 0}
            onClick={() => actions.requestRide(pickup, dropoff)}
          >
            {drivers.length === 0 ? '⚠️ Seed drivers first' : 'Request Ride'}
          </button>
        )}

        {isSearching && (
          <div className="pp-searching-state">
            <div className="pp-searching-indicator">
              <div className="pp-dot-row">
                <div className="pp-dot" />
                <div className="pp-dot" />
                <div className="pp-dot" />
              </div>
              Searching for drivers...
            </div>
            <button
              className="btn-ghost w-full"
              style={{ fontSize: 12 }}
              onClick={() => actions.cancelTrip(tripId, 'Cancelled while searching')}
            >
              Cancel Request
            </button>
          </div>
        )}

        {canCancel && !isSearching && (
          <button
            className="btn-danger-outline w-full"
            onClick={() => actions.cancelTrip(tripId, 'Cancelled by passenger')}
          >
            Cancel Ride
          </button>
        )}

        {(isCompleted || isCancelled) && (
          <button className="btn-primary w-full" onClick={actions.reset}>
            {isCompleted ? 'New Booking' : 'Try Again'}
          </button>
        )}
      </div>

      {/* ── Section 6: Notifications ── */}
      <div className="pp-notif-section">
        <div className="pp-notif-title">Notifications</div>
        <div className="pp-notif-list">
          {passengerNotifs.length === 0
            ? <div className="pp-notif-empty">No notifications yet</div>
            : passengerNotifs.map(n => (
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
