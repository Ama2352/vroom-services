/**
 * ControlBar.jsx – Demo control strip: main actions + playback controls.
 */
import { useState, useRef, useCallback } from 'react';
import {
  PlusCircle, RotateCcw, Play, Pause, Gauge
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

    try {
      // 1. Seed
      await run('seed', () => actions.seedDrivers());
      await delay(1000);

      // 2. Request
      const tid = await run('request', () => actions.requestRide());
      if (!tid) throw new Error('Request failed');
      await delay(2000); // Wait for matching

      // 3. Accept (Wait for driver move)
      await run('accept', () => actions.acceptTrip(tid));
      
      // 4. Start (Wait for trip move)
      await delay(1000);
      await run('start', () => actions.startTrip(tid));
      
      // 5. Complete
      await delay(1000);
      await run('complete', () => actions.completeTrip(tid));
    } catch (err) {
      console.error('AutoPlay failed:', err);
    } finally {
      actions.setAutoPlay(false);
    }
  }, [actions, speed, run]);

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
          {loading === 'seed' ? <Spinner /> : <PlusCircle size={14} />}
          Seed Drivers
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
