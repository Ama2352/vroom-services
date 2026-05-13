import { useDemo, TRIP_STATUS } from '../store/demoStore';
import './TripProgress.css';

const STEPS = [
  { key: 'request',  label: 'Request',  icon: '📋', statuses: [TRIP_STATUS.IDLE] },
  { key: 'search',   label: 'Searching',icon: '🔍', statuses: [TRIP_STATUS.SEARCHING] },
  { key: 'matched',  label: 'Matched',  icon: '🔗', statuses: [TRIP_STATUS.ASSIGNED] },
  { key: 'accepted', label: 'Accepted', icon: '✅', statuses: [TRIP_STATUS.ACCEPTED, TRIP_STATUS.COMING] },
  { key: 'ontrip',   label: 'On Trip',  icon: '🛣️',  statuses: [TRIP_STATUS.ON_TRIP] },
  { key: 'done',     label: 'Done',     icon: '🎉', statuses: [TRIP_STATUS.COMPLETED] },
];

function getStepIndex(tripStatus) {
  const idx = STEPS.findIndex(s => s.statuses.includes(tripStatus));
  return idx === -1 ? 0 : idx;
}

export default function TripProgress() {
  const { state } = useDemo();
  const { tripStatus } = state;

  const currentIdx = getStepIndex(tripStatus);
  const isCancelled = tripStatus === TRIP_STATUS.CANCELLED;

  return (
    <div className={`trip-progress ${isCancelled ? 'tp-cancelled' : ''}`}>
      <div className="tp-steps">
        {STEPS.map((step, i) => {
          const phase =
            isCancelled            ? 'future'
            : i < currentIdx       ? 'done'
            : i === currentIdx     ? 'active'
            : 'future';

          return (
            <div key={step.key} className={`tp-step tp-step--${phase}`}>
              {/* Connector line to the left (not for first step) */}
              {i > 0 && (
                <div className={`tp-connector ${i <= currentIdx && !isCancelled ? 'tp-connector--filled' : ''}`} />
              )}
              <div className="tp-node">
                <span className="tp-node-icon">{step.icon}</span>
                {phase === 'active' && <div className="tp-pulse" />}
              </div>
              <span className="tp-label">{step.label}</span>
            </div>
          );
        })}
      </div>

      {isCancelled && (
        <div className="tp-cancelled-badge">❌ Trip Cancelled</div>
      )}
    </div>
  );
}
