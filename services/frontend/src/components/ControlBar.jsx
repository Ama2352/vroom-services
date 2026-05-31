import { useState, useCallback } from 'react';
import { PlusCircle, RotateCcw, Users } from 'lucide-react';
import { useDemo, TRIP_STATUS } from '../store/demoStore';
import './ControlBar.css';

const WS_COLOR = {
  connected:    '#22C55E',
  disconnected: '#EF4444',
  connecting:   '#F59E0B',
};

export default function ControlBar() {
  const { state, actions } = useDemo();
  const { tripStatus, drivers, wsStatus } = state;
  const [loading, setLoading] = useState('');

  const isIdle = tripStatus === TRIP_STATUS.IDLE;

  const run = useCallback(async (key, fn) => {
    setLoading(key);
    try { return await fn(); } finally { setLoading(''); }
  }, []);

  const wsLabel =
    wsStatus === 'connected'    ? 'LIVE' :
    wsStatus === 'disconnected' ? 'OFFLINE' : 'CONNECTING';

  return (
    <div className="controlbar">
      {/* Primary actions */}
      <div className="action-group">
        <button
          id="btn-seed"
          className="btn-ghost"
          disabled={!isIdle || !!loading}
          onClick={() => run('seed', () => actions.seedDrivers())}
        >
          {loading === 'seed' ? <Spinner /> : <PlusCircle size={14} />}
          {drivers.length > 0 ? 'Re-seed' : 'Seed Drivers'}
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

      {/* System health (right side) */}
      <div className="controlbar-health">
        <div className="controlbar-sep" />

        <div className="health-item">
          <span
            style={{
              width: 7, height: 7,
              borderRadius: '50%',
              background: WS_COLOR[wsStatus] ?? '#F59E0B',
              flexShrink: 0,
              boxShadow: wsStatus === 'connected' ? '0 0 5px rgba(34,197,94,0.6)' : 'none',
            }}
          />
          WS {wsLabel}
        </div>

        <div className={`health-item ${drivers.length > 0 ? 'health-item-green' : ''}`}>
          <Users size={12} />
          {drivers.length} online
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
