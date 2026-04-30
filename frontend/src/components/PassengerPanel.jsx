/**
 * PassengerPanel.jsx – Passenger mock app (left column).
 * Simulates a Grab-like booking UI.
 */
import { useState } from 'react';
import { MapPin, Navigation, User, Clock, CreditCard } from 'lucide-react';
import { useDemo, PICKUP_PRESETS, DROPOFF_PRESETS, TRIP_STATUS } from '../store/demoStore';
import './PassengerPanel.css';

export default function PassengerPanel() {
  const { state, actions } = useDemo();
  const { tripStatus, pickup, dropoff, notifications, tripId, estimatedFare, estimatedTime } = state;
  const isIdle = tripStatus === TRIP_STATUS.IDLE;

  const passengerNotifs = notifications
    .filter(n => n.side === 'passenger')
    .slice(0, 4);

  return (
    <div className="panel passenger-panel">
      {/* Header */}
      <div className="panel-header">
        <div className="panel-avatar">
          <User size={16} />
        </div>
        <div>
          <div className="panel-title">Passenger</div>
          <div className="panel-sub">passenger-demo</div>
        </div>
        <div className="passenger-badge">
          <span className="dot-green" />
          Online
        </div>
      </div>

      <div className="divider" />

      {/* Location inputs */}
      <div className="panel-section">
        <div className="loc-field">
          <div className="loc-icon pickup-icon">
            <MapPin size={14} />
          </div>
          <div className="loc-content">
            <label className="loc-label">Pickup</label>
            <select
              id="pickup-select"
              className="loc-select"
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

        <div className="loc-connector">
          <div className="connector-line" />
        </div>

        <div className="loc-field">
          <div className="loc-icon dropoff-icon">
            <Navigation size={14} />
          </div>
          <div className="loc-content">
            <label className="loc-label">Destination</label>
            <select
              id="dropoff-select"
              className="loc-select"
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
      </div>

      <div className="divider" />

      {/* Fare estimate */}
      <div className="panel-section">
        <div className="fare-card">
          <div className="fare-row">
            <span className="fare-label"><Clock size={13} /> Est. Time</span>
            <span className="fare-value">~{estimatedTime} min</span>
          </div>
          <div className="fare-row">
            <span className="fare-label"><CreditCard size={13} /> Fare</span>
            <span className="fare-value fare-price">{estimatedFare.toLocaleString('vi-VN')} VND</span>
          </div>
        </div>

        {/* Passenger Actions */}
        <div className="passenger-actions mt-6">
          {isIdle && (
            <button 
              className="btn-primary w-full" 
              disabled={state.drivers.length === 0}
              onClick={() => actions.requestRide(pickup, dropoff)}
            >
              Request Ride
            </button>
          )}

          {tripStatus === TRIP_STATUS.SEARCHING && (
            <div className="flex flex-col gap-2">
              <button className="btn-primary w-full opacity-75 cursor-not-allowed" disabled>
                <div className="flex items-center justify-center gap-2">
                  <span className="dot-bounce" />
                  Searching for Drivers...
                </div>
              </button>
              <button 
                className="btn-ghost w-full text-sm"
                onClick={() => actions.cancelTrip(tripId, 'Cancelled while searching')}
              >
                Cancel Request
              </button>
            </div>
          )}

          {(tripStatus === TRIP_STATUS.ASSIGNED || tripStatus === TRIP_STATUS.ACCEPTED || tripStatus === TRIP_STATUS.COMING) && (
            <button 
              className="btn-danger-outline w-full"
              onClick={() => actions.cancelTrip(tripId, 'Cancelled by passenger')}
            >
              Cancel Ride
            </button>
          )}
        </div>
      </div>

      <div className="divider" />

      {/* Notifications */}
      <div className="panel-section notif-section">
        <div className="section-title">Notifications</div>
        <div className="notif-list">
          {passengerNotifs.length === 0 && (
            <div className="notif-empty">No notifications yet</div>
          )}
          {passengerNotifs.map(n => (
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

      {/* Trip state card */}
      {tripStatus !== TRIP_STATUS.IDLE && (
        <div className="trip-state-card">
          <TripStateVisual status={tripStatus} driver={state.assignedDriver} />
        </div>
      )}
    </div>
  );
}

function TripStateVisual({ status, driver }) {
  const messages = {
    [TRIP_STATUS.SEARCHING]:  { icon: '🔍', text: 'Finding the best driver for you…' },
    [TRIP_STATUS.ASSIGNED]:   { icon: '🚗', text: `${driver?.name ?? 'Driver'} is assigned to your trip.` },
    [TRIP_STATUS.COMING]:     { icon: '📍', text: `${driver?.name ?? 'Driver'} is heading to your pickup.` },
    [TRIP_STATUS.ON_TRIP]:    { icon: '🛣️',  text: 'You are on your way to destination!' },
    [TRIP_STATUS.COMPLETED]:  { icon: '🎉', text: 'Trip completed! Have a great day.' },
    [TRIP_STATUS.CANCELLED]:  { icon: '❌', text: 'Trip has been cancelled.' },
  };
  const m = messages[status];
  if (!m) return null;

  return (
    <div className="tsv-inner">
      <div className="tsv-icon">{m.icon}</div>
      <p className="tsv-text">{m.text}</p>
      {status === TRIP_STATUS.SEARCHING && (
        <div className="search-dots">
          <span className="dot-bounce" />
          <span className="dot-bounce" />
          <span className="dot-bounce" />
        </div>
      )}
      {driver && status !== TRIP_STATUS.COMPLETED && status !== TRIP_STATUS.CANCELLED && (
        <div className="driver-chip">
          <span>{driver.avatar}</span>
          <span>{driver.name}</span>
        </div>
      )}
      {(status === TRIP_STATUS.COMPLETED || status === TRIP_STATUS.CANCELLED) && (
        <button className="tsv-reset-btn" onClick={() => window.location.reload()}>
          New Booking
        </button>
      )}
    </div>
  );
}
