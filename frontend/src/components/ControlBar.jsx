import { useState, useCallback } from 'react';
import { PlusCircle, RotateCcw } from 'lucide-react';
import { useDemo, TRIP_STATUS } from '../store/demoStore';
import './ControlBar.css';

export default function ControlBar() {
  const { state, actions } = useDemo();
  const { tripStatus, drivers } = state;
  const [loading, setLoading] = useState('');

  const isIdle = tripStatus === TRIP_STATUS.IDLE;

  const run = useCallback(async (key, fn) => {
    setLoading(key);
    try { return await fn(); } finally { setLoading(''); }
  }, []);

  return (
    <div className="controlbar">
      {/* Step guide */}
      <div className="guide-steps">
        <div className={`guide-step ${drivers.length > 0 ? 'done' : ''}`}>
          <span className="step-num">1</span>
          <span>Seed Drivers</span>
        </div>
        <div className="guide-arrow">→</div>
        <div className={`guide-step ${tripStatus !== TRIP_STATUS.IDLE ? 'done' : ''}`}>
          <span className="step-num">2</span>
          <span>Request Ride</span>
        </div>
        <div className="guide-arrow">→</div>
        <div className={`guide-step ${tripStatus === TRIP_STATUS.COMPLETED ? 'done' : ''}`}>
          <span className="step-num">3</span>
          <span>Simulate &amp; Complete</span>
        </div>
      </div>

      <div className="controlbar-divider" />

      {/* Action buttons */}
      <div className="action-group">
        <button
          id="btn-seed"
          className="btn-ghost"
          disabled={!isIdle || !!loading}
          onClick={() => run('seed', () => actions.seedDrivers())}
        >
          {loading === 'seed' ? <Spinner /> : <PlusCircle size={14} />}
          {drivers.length > 0 ? 'Re-seed Drivers' : 'Seed Drivers'}
        </button>

        <button
          id="btn-reset"
          className="btn-danger"
          disabled={!!loading}
          onClick={() => run('reset', () => actions.reset())}
        >
          {loading === 'reset' ? <Spinner /> : <RotateCcw size={14} />}
          Reset
        </button>
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
