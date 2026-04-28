/**
 * ControlBar.jsx – Demo control strip: main actions + playback controls.
 */
import { useState, useRef, useCallback } from 'react';
import {
  Users, Navigation2, Truck, CheckSquare, RotateCcw,
  Play, Pause, Gauge, StepForward, Info, PlayCircle, XCircle
} from 'lucide-react';
import { useDemo, TRIP_STATUS } from '../store/demoStore';
import './ControlBar.css';

const SPEEDS = [1, 2, 4];

export default function ControlBar() {
  const { state, actions } = useDemo();
  const { tripStatus, drivers, assignedDriver, tripId, autoPlay, speed, driverMoving } = state;
  const [loading, setLoading] = useState('');
  const autoPlayRef = useRef(null);

  const isIdle      = tripStatus === TRIP_STATUS.IDLE;
  const isSearching = tripStatus === TRIP_STATUS.SEARCHING;
  const isAssigned  = tripStatus === TRIP_STATUS.ASSIGNED;
  const isAccepted  = tripStatus === TRIP_STATUS.ACCEPTED;
  const isComing    = tripStatus === TRIP_STATUS.COMING;
  const isOnTrip    = tripStatus === TRIP_STATUS.ON_TRIP;
  const isCompleted = tripStatus === TRIP_STATUS.COMPLETED;

  const canSeed    = isIdle;
  const canRequest = isIdle && drivers.length > 0;
  const canAccept  = isAssigned;
  const canStart   = isAccepted && !driverMoving;
  const canComplete = isOnTrip && !driverMoving;
  const canCancel   = !isIdle && !isCompleted && tripStatus !== TRIP_STATUS.CANCELLED;

  const run = useCallback(async (key, fn) => {
    setLoading(key);
    try { await fn(); } finally { setLoading(''); }
  }, []);

  /* Auto play: runs the whole demo flow */
  const startAutoPlay = useCallback(async () => {
    actions.setAutoPlay(true);
    const delay = ms => new Promise(r => setTimeout(r, ms / speed));

    await run('seed', () => actions.seedDrivers());
    await delay(800);
    const tid = await run('request', async () => actions.requestRide(state.pickup, state.dropoff));
    await delay(1000);
    if (isAssigned || state.tripStatus === TRIP_STATUS.ASSIGNED) {
      await run('accept', () => actions.acceptTrip(state.tripId));
    }
    await delay(600);
    await run('start', () => actions.startTrip(state.tripId));
    await delay(1500); // Wait for simulation
    await run('complete', () => actions.completeTrip(state.tripId));
    actions.setAutoPlay(false);
  }, [state, actions, speed, run]);

  return (
    <div className="controlbar">
      {/* Guide */}
      <div className="guide-steps">
        <div className="guide-step">
          <span className="step-num">1</span>
          <span>Seed Drivers</span>
        </div>
        <div className="guide-arrow">→</div>
        <div className="guide-step">
          <span className="step-num">2</span>
          <span>Request Ride</span>
        </div>
        <div className="guide-arrow">→</div>
        <div className="guide-step">
          <span className="step-num">3</span>
          <span>Simulate &amp; Complete</span>
        </div>
      </div>

      <div className="controlbar-divider" />

      {/* Main actions */}
      <div className="action-group">
        <button
          id="btn-seed"
          className="btn-ghost"
          disabled={!canSeed || !!loading}
          onClick={() => run('seed', () => actions.seedDrivers())}
        >
          {loading === 'seed' ? <Spinner /> : <Users size={14} />}
          Seed Drivers
        </button>

        <button
          id="btn-request"
          className="btn-primary"
          disabled={!canRequest || !!loading || isSearching}
          onClick={() => run('request', () => actions.requestRide(state.pickup, state.dropoff))}
        >
          {loading === 'request' ? <Spinner /> : <Navigation2 size={14} />}
          Request Ride
        </button>

        <button
          id="btn-accept"
          className="btn-ghost"
          disabled={!canAccept || !!loading}
          onClick={() => run('accept', () => actions.acceptTrip(tripId))}
        >
          {loading === 'accept' ? <Spinner /> : <CheckSquare size={14} />}
          Accept
        </button>

        <button
          id="btn-start"
          className="btn-ghost"
          disabled={!canStart || !!loading}
          onClick={() => run('start', () => actions.startTrip(tripId))}
        >
          {loading === 'start' ? <Spinner /> : <PlayCircle size={14} />}
          Start
        </button>


        <button
          id="btn-complete"
          className="btn-success"
          disabled={!canComplete || !!loading}
          onClick={() => run('complete', () => actions.completeTrip(tripId))}
        >
          {loading === 'complete' ? <Spinner /> : <CheckSquare size={14} />}
          Complete
        </button>

        <button
          id="btn-cancel"
          className="btn-danger-outline"
          disabled={!canCancel || !!loading}
          onClick={() => run('cancel', () => actions.cancelTrip(tripId))}
        >
          {loading === 'cancel' ? <Spinner /> : <XCircle size={14} />}
          Cancel
        </button>

        <button
          id="btn-reset"
          className="btn-danger"
          onClick={() => { actions.reset(); setLoading(''); }}
        >
          <RotateCcw size={14} />
          Reset
        </button>
      </div>

      <div className="controlbar-divider" />

      {/* Playback controls */}
      <div className="playback-group">
        <button
          id="btn-autoplay"
          className={`btn-ghost ${autoPlay ? 'active' : ''}`}
          disabled={autoPlay || (!isIdle && !isCompleted)}
          onClick={startAutoPlay}
        >
          {autoPlay ? <Pause size={14} /> : <Play size={14} />}
          {autoPlay ? 'Running…' : 'Auto Play'}
        </button>

        <div className="speed-group">
          <Gauge size={13} className="speed-icon" />
          {SPEEDS.map(s => (
            <button
              key={s}
              id={`btn-speed-${s}`}
              className={`speed-btn ${speed === s ? 'active' : ''}`}
              onClick={() => actions.setSpeed(s)}
            >
              ×{s}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <span className="spinner" aria-label="loading">
      <span /><span /><span />
    </span>
  );
}
