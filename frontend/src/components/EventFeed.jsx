import { useDemo } from '../store/demoStore';
import './EventFeed.css';

const SERVICE_COLOR = {
  ride:         '#6C63FF',
  dispatch:     '#06B6D4',
  notification: '#F59E0B',
  system:       '#4B5563',
};

function fmt(ts) {
  return ts.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export default function EventFeed() {
  const { state } = useDemo();
  const recent = state.events.slice(0, 8);

  return (
    <div className="event-feed">
      <span className="ef-label">Live Events</span>
      <div className="ef-scroll">
        {recent.length === 0 ? (
          <span className="ef-empty">No events yet — seed drivers and request a ride</span>
        ) : (
          recent.map(evt => (
            <div key={evt.id} className="ef-chip">
              <span
                className="ef-dot"
                style={{ background: SERVICE_COLOR[evt.service] ?? '#4B5563' }}
              />
              <span className="ef-type">{evt.type}</span>
              <span className="ef-svc">{evt.service}</span>
              <span className="ef-time mono">{fmt(evt.ts)}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
