/**
 * MapView.jsx – Leaflet map with drivers, pickup/dropoff markers, and route lines.
 */
import { useEffect, useRef } from 'react';
import { MapContainer, TileLayer, Marker, Popup, Polyline, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { useDemo, HCMC_CENTER, TRIP_STATUS } from '../store/demoStore';
import './MapView.css';

/* Fix leaflet default icon paths broken by Vite */
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  iconUrl:       'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  shadowUrl:     'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
});

/* ── Custom SVG icons ── */
function makeIcon(svg, size = [36, 36], anchor = [18, 36]) {
  return L.divIcon({ html: svg, className: '', iconSize: size, iconAnchor: anchor, popupAnchor: [0, -36] });
}

const PICKUP_ICON = makeIcon(`
  <svg width="36" height="42" viewBox="0 0 36 42" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle cx="18" cy="18" r="16" fill="#22C55E" fill-opacity="0.15" stroke="#22C55E" stroke-width="2"/>
    <circle cx="18" cy="18" r="8" fill="#22C55E"/>
    <line x1="18" y1="34" x2="18" y2="42" stroke="#22C55E" stroke-width="2"/>
  </svg>
`, [36, 42], [18, 42]);

const DROPOFF_ICON = makeIcon(`
  <svg width="28" height="36" viewBox="0 0 28 36" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M14 0C6.268 0 0 6.268 0 14c0 9.333 14 36 14 36s14-26.667 14-36C28 6.268 21.732 0 14 0z" fill="#EF4444"/>
    <circle cx="14" cy="14" r="6" fill="white"/>
  </svg>
`, [28, 36], [14, 36]);

const PASSENGER_ICON = makeIcon(`
  <div style="
    width:34px;height:34px;border-radius:50%;
    background:linear-gradient(135deg,#6C63FF,#8B84FF);
    border:2px solid #fff;
    display:flex;align-items:center;justify-content:center;
    font-size:17px;box-shadow:0 0 12px rgba(108,99,255,0.5);
  ">🧑‍💼</div>
`, [34, 34], [17, 17]);

function driverIcon(avatar, isAssigned) {
  const glow = isAssigned ? 'box-shadow:0 0 16px rgba(34,197,94,0.7);' : '';
  const border = isAssigned ? 'border:2px solid #22C55E;' : 'border:2px solid #3B82F6;';
  return makeIcon(`
    <div style="
      width:32px;height:32px;border-radius:50%;
      background:var(--surface-2,#1E2334);
      ${border}
      display:flex;align-items:center;justify-content:center;
      font-size:16px;${glow}
      position:relative;
    ">${avatar}
    ${isAssigned ? '<div style="position:absolute;inset:-5px;border-radius:50%;border:2px solid #22C55E;opacity:0.5;animation:pulse-ring 1.8s ease-out infinite;"></div>' : ''}
    </div>
  `, [32, 32], [16, 16]);
}

/* ── Re-center map when pickup changes ── */
function MapController({ center }) {
  const map = useMap();
  useEffect(() => {
    map.setView(center, map.getZoom(), { animate: true });
  }, [center, map]);
  return null;
}

export default function MapView() {
  const { state } = useDemo();
  const { drivers, pickup, dropoff, assignedDriver, tripStatus } = state;

  const showRoute = tripStatus !== TRIP_STATUS.IDLE && tripStatus !== TRIP_STATUS.SEARCHING;
  const showTrip  = tripStatus === TRIP_STATUS.ON_TRIP || tripStatus === TRIP_STATUS.COMPLETED;

  return (
    <div className="map-wrapper">
      <MapContainer
        center={HCMC_CENTER}
        zoom={14}
        className="leaflet-map"
        zoomControl={true}
        scrollWheelZoom={true}
      >
        {/* Dark tile layer */}
        <TileLayer
          url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
          attribution=""
          maxZoom={19}
        />

        <MapController center={[pickup.lat, pickup.lng]} />

        {/* Pickup marker */}
        <Marker position={[pickup.lat, pickup.lng]} icon={PICKUP_ICON}>
          <Popup>
            <strong>📍 Pickup</strong><br />{pickup.label}
          </Popup>
        </Marker>

        {/* Dropoff marker */}
        <Marker position={[dropoff.lat, dropoff.lng]} icon={DROPOFF_ICON}>
          <Popup>
            <strong>🏁 Dropoff</strong><br />{dropoff.label}
          </Popup>
        </Marker>

        {/* Passenger marker (when trip is active) */}
        {tripStatus !== TRIP_STATUS.IDLE && (
          <Marker position={[pickup.lat, pickup.lng]} icon={PASSENGER_ICON}>
            <Popup>Passenger waiting here</Popup>
          </Marker>
        )}

        {/* Driver markers */}
        {drivers.map(d => {
          const isAssigned = assignedDriver?.id === d.id;
          return (
            <Marker
              key={d.id}
              position={[d.lat, d.lng]}
              icon={driverIcon(d.avatar, isAssigned)}
            >
              <Popup>
                <strong>{d.avatar} {d.name}</strong><br />
                {isAssigned ? '✅ Assigned to your trip' : '🟢 Available'}
              </Popup>
            </Marker>
          );
        })}

        {/* Route: driver → pickup */}
        {showRoute && assignedDriver && (
          <Polyline
            positions={[
              [assignedDriver.lat, assignedDriver.lng],
              [pickup.lat, pickup.lng],
            ]}
            color="#06B6D4"
            weight={3}
            opacity={0.7}
            dashArray="8 6"
          />
        )}

        {/* Route: pickup → dropoff */}
        {showTrip && (
          <Polyline
            positions={[
              [pickup.lat,  pickup.lng],
              [dropoff.lat, dropoff.lng],
            ]}
            color="#6C63FF"
            weight={4}
            opacity={0.8}
          />
        )}
      </MapContainer>

      {/* Map overlay info */}
      <div className="map-legend">
        <div className="legend-item"><span className="legend-dot" style={{ background: '#22C55E' }} />Pickup</div>
        <div className="legend-item"><span className="legend-dot" style={{ background: '#EF4444' }} />Dropoff</div>
        <div className="legend-item"><span className="legend-dot" style={{ background: '#3B82F6' }} />Driver</div>
        {assignedDriver && <div className="legend-item"><span className="legend-dot glow-green" style={{ background: '#22C55E' }} />Assigned</div>}
      </div>

      {tripStatus === TRIP_STATUS.SEARCHING && (
        <div className="map-searching-overlay">
          <div className="searching-pulse">
            <div className="sp-ring sp-ring-1" />
            <div className="sp-ring sp-ring-2" />
            <div className="sp-ring sp-ring-3" />
            <span className="sp-label">Searching…</span>
          </div>
        </div>
      )}
    </div>
  );
}
