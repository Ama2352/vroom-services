/**
 * demoStore.js – Centralized state for the Vroom demo UI.
 * Uses useReducer + Context for clean state sharing across panels.
 */
import { createContext, useContext, useReducer, useCallback, useRef, useEffect } from 'react';
import axios from 'axios';

/* ── Base URLs (docker-compose) ── */
export const API = {
  user:         'http://localhost:8081',
  ride:         'http://localhost:8082',
  dispatch:     'http://localhost:8083',
  notification: 'http://localhost:8084',
  dispatchWS:   'ws://localhost:8083/v1/dispatch/ws/location',
  notificationWS: 'ws://localhost:8084/v1/ws',
};

/* ── Trip lifecycle states ── */
export const TRIP_STATUS = {
  IDLE:       'Idle',
  SEARCHING:  'Searching Driver',
  ASSIGNED:   'Driver Assigned',
  ACCEPTED:   'Accepted',
  COMING:     'Driver Coming',
  ON_TRIP:    'On Trip',
  COMPLETED:  'Completed',
  CANCELLED:  'Cancelled',
};

/* ── Ho Chi Minh City area coordinates ── */
export const HCMC_CENTER = [10.7769, 106.7009];

export const DRIVER_SEEDS = [
  { id: '550e8400-e29b-41d4-a716-446655440001', name: 'Nguyen Van An', lat: 10.7800, lng: 106.6970, avatar: '🧑' },
  { id: '550e8400-e29b-41d4-a716-446655440002', name: 'Tran Thi Bich', lat: 10.7750, lng: 106.7050, avatar: '👩' },
  { id: '550e8400-e29b-41d4-a716-446655440003', name: 'Le Van Cuong',  lat: 10.7820, lng: 106.7080, avatar: '👨' },
];

export const PICKUP_PRESETS = [
  { label: 'Ben Thanh Market',  lat: 10.7724, lng: 106.6980 },
  { label: 'Nguyen Hue Walk',   lat: 10.7754, lng: 106.7028 },
  { label: 'Bitexco Tower',     lat: 10.7718, lng: 106.7040 },
];

export const DROPOFF_PRESETS = [
  { label: 'Tan Son Nhat Airport', lat: 10.8184, lng: 106.6697 },
  { label: 'Landmark 81',          lat: 10.7947, lng: 106.7218 },
  { label: 'Saigon Zoo',           lat: 10.7876, lng: 106.7115 },
];

/* ─────────────────────────────────────────────
   State shape
───────────────────────────────────────────── */
const initialState = {
  tripStatus: TRIP_STATUS.IDLE,
  tripId: null,
  drivers: [],           // seeded drivers with positions
  assignedDriver: null,
  pickup: PICKUP_PRESETS[0],
  dropoff: DROPOFF_PRESETS[0],
  events: [],            // timeline events
  notifications: [],     // passenger + driver toasts
  apiLog: null,          // last API call info
  driverMoving: false,
  autoPlay: false,
  speed: 1,              // playback speed multiplier
  stepMode: false,
};

/* ─────────────────────────────────────────────
   Reducer
───────────────────────────────────────────── */
function reducer(state, action) {
  switch (action.type) {
    case 'SET_STATUS':
      return { ...state, tripStatus: action.payload };

    case 'SET_TRIP_ID':
      return { ...state, tripId: action.payload };

    case 'SEED_DRIVERS':
      return { ...state, drivers: DRIVER_SEEDS.map(d => ({ ...d })) };

    case 'ASSIGN_DRIVER':
      return { ...state, assignedDriver: action.payload, tripStatus: TRIP_STATUS.ASSIGNED };

    case 'UPDATE_DRIVER_POS': {
      const updated = state.drivers.map(d =>
        d.id === action.payload.id ? { ...d, lat: action.payload.lat, lng: action.payload.lng } : d
      );
      const assigned = state.assignedDriver?.id === action.payload.id
        ? { ...state.assignedDriver, lat: action.payload.lat, lng: action.payload.lng }
        : state.assignedDriver;
      return { ...state, drivers: updated, assignedDriver: assigned };
    }

    case 'SET_PICKUP':
      return { ...state, pickup: action.payload };

    case 'SET_DROPOFF':
      return { ...state, dropoff: action.payload };

    case 'PUSH_EVENT':
      return { ...state, events: [action.payload, ...state.events].slice(0, 50) };

    case 'PUSH_NOTIFICATION':
      return { ...state, notifications: [action.payload, ...state.notifications].slice(0, 30) };

    case 'DISMISS_NOTIFICATION':
      return { ...state, notifications: state.notifications.filter(n => n.id !== action.payload) };

    case 'SET_API_LOG':
      return { ...state, apiLog: action.payload };

    case 'SET_DRIVER_MOVING':
      return { ...state, driverMoving: action.payload };

    case 'SET_AUTO_PLAY':
      return { ...state, autoPlay: action.payload };

    case 'SET_SPEED':
      return { ...state, speed: action.payload };

    case 'SET_STEP_MODE':
      return { ...state, stepMode: action.payload };

    case 'RESET':
      return {
        ...initialState,
        pickup:  state.pickup,
        dropoff: state.dropoff,
      };

    default:
      return state;
  }
}

/* ─────────────────────────────────────────────
   Context
───────────────────────────────────────────── */
const StoreCtx = createContext(null);

export function DemoStoreProvider({ children }) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const wsRef = useRef(null);
  const processedEventIdsRef = useRef(new Set());

  /* ── WebSocket Connection for Driver Locations ── */
  useEffect(() => {
    const connect = () => {
      console.log(`Connecting to Dispatch WS: ${API.dispatchWS}`);
      const ws = new WebSocket(API.dispatchWS);
      
      ws.onopen = () => {
        console.log('✅ Dispatch WebSocket connected');
        wsRef.current = ws;
      };
      
      ws.onclose = () => {
        console.log('❌ Dispatch WebSocket disconnected, retrying in 3s...');
        wsRef.current = null;
        setTimeout(connect, 3000);
      };
      
      ws.onerror = (err) => {
        console.error('WebSocket Error:', err);
      };
    };

    connect();
    return () => {
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  /* ── WebSocket Connection for Notifications (Real-time events) ── */
  useEffect(() => {
    const demoUserId = 'c10a8c2f-3d6a-491c-acbb-d313cd4d625f';

    const connectNotif = () => {
      const wsUrl = `${API.notificationWS}?userId=${demoUserId}`;
      const ws = new WebSocket(wsUrl);

      ws.onmessage = (event) => {
        try {
          const data = jsonParse(event.data);
          // Persistent deduplication via Ref
          const uid = data.id || data.correlation_id || `${data.event_type}-${JSON.stringify(data.payload)}`;
          if (processedEventIdsRef.current.has(uid)) return;
          processedEventIdsRef.current.add(uid);
          
          // Keep set size manageable
          if (processedEventIdsRef.current.size > 200) {
            const first = processedEventIdsRef.current.values().next().value;
            processedEventIdsRef.current.delete(first);
          }

          handleIncomingEvent(data);
        } catch (err) { console.error('WS Message Error:', err); }
      };

      ws.onopen = () => console.log('✅ Notification WS connected');
      ws.onclose = () => setTimeout(connectNotif, 3000);
    };

    connectNotif();
  }, []);

  const handleIncomingEvent = (evt) => {
    const { event_type, payload } = evt;
    let data = {};
    try {
      data = typeof payload === 'string' ? JSON.parse(payload) : payload;
    } catch (e) {
      data = payload;
    }

    console.log(`[EVENT] ${event_type}`, data);

    switch (event_type) {
      case 'Trip.Requested':
        pushEvent('Trip.Requested', 'ride', { tripId: data.id, status: 'Processing' });
        break;

      case 'Trip.Matched': {
        const driverId = data.driver_id;
        // In a real app, we'd fetch driver details. For demo, we find in our seeds.
        const matchedDriver = DRIVER_SEEDS.find(d => d.id === driverId) || DRIVER_SEEDS[0];
        
        dispatch({ type: 'ASSIGN_DRIVER', payload: matchedDriver });
        pushEvent('Trip.Matched', 'dispatch', { tripId: data.id, driverId, driverName: matchedDriver.name });
        notify('passenger', `Driver matched: ${matchedDriver.name} is on the way!`, 'success');
        notify('driver', `New ride request! Heading to pickup.`, 'info');
        break;
      }

      case 'Trip.Accepted':
        dispatch({ type: 'SET_STATUS', payload: TRIP_STATUS.ACCEPTED });
        pushEvent('Trip.Accepted', 'ride', { tripId: data.id });
        notify('passenger', 'Driver is on the way to your location!', 'success');
        notify('driver', 'Trip accepted! Navigating to passenger.', 'info');
        break;

      case 'Trip.Started':
        dispatch({ type: 'SET_STATUS', payload: TRIP_STATUS.ON_TRIP });
        pushEvent('Trip.Started', 'ride', { tripId: data.id });
        notify('passenger', 'Trip has started! Enjoy your ride 🚗', 'success');
        break;

      case 'Trip.Cancelled':
        dispatch({ type: 'SET_STATUS', payload: TRIP_STATUS.CANCELLED });
        pushEvent('Trip.Cancelled', 'ride', { reason: data.reason });
        notify('passenger', 'Trip has been cancelled.', 'warning');
        notify('driver', 'Trip has been cancelled by the passenger.', 'warning');
        break;

      case 'Trip.Completed':
        dispatch({ type: 'SET_STATUS', payload: TRIP_STATUS.COMPLETED });
        pushEvent('Trip.Completed', 'ride', { tripId: data.id });
        notify('passenger', '🎉 Trip completed! Thanks for riding with Vroom.', 'success');
        break;
      
      default:
        // Generic event logging
        pushEvent(event_type, 'system', data);
    }
  };

  // Helper for safe JSON parsing
  const jsonParse = (str) => {
    try { return JSON.parse(str); } catch (e) { return str; }
  };

  const sendLocationUpdate = useCallback((driverId, lat, lng) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      const msg = { driver_id: driverId, lat, lng };
      wsRef.current.send(JSON.stringify(msg));
    }
  }, []);

  /* Helper: record api call */
  const logApi = useCallback((method, url, payload, status, response) => {
    dispatch({ type: 'SET_API_LOG', payload: { method, url, payload, status, response, ts: new Date() } });
  }, []);

  /* Helper: push timeline event */
  const pushEvent = useCallback((type, service, detail = {}) => {
    dispatch({
      type: 'PUSH_EVENT',
      payload: {
        id: `evt-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        type,
        service,
        detail,
        ts: new Date(),
      },
    });
  }, []);

  /* Helper: push notification */
  const notify = useCallback((side, message, variant = 'info') => {
    const id = `notif-${Date.now()}-${Math.random()}`;
    dispatch({ type: 'PUSH_NOTIFICATION', payload: { id, side, message, variant, ts: new Date() } });
    setTimeout(() => dispatch({ type: 'DISMISS_NOTIFICATION', payload: id }), 12000); // 12s
  }, []);

  /* ── Actions ── */
  const actions = {

    seedDrivers: async () => {
      dispatch({ type: 'SEED_DRIVERS' });
      pushEvent('Drivers.Seeded', 'dispatch', { count: DRIVER_SEEDS.length });
      notify('driver', '3 drivers are now online and ready.', 'success');

      // Fire location updates to dispatch service
      for (const d of DRIVER_SEEDS) {
        // 1. REST update (Fixed payload keys: lat/lng)
        const payload = { lat: d.lat, lng: d.lng };
        try {
          const res = await axios.put(`${API.dispatch}/v1/drivers/${d.id}/location`, payload);
          logApi('PUT', `/v1/drivers/${d.id}/location`, payload, res.status, res.data);
        } catch (err) {
          logApi('PUT', `/v1/drivers/${d.id}/location`, payload, err.response?.status ?? 0, err.message);
        }

        // 2. WebSocket update
        sendLocationUpdate(d.id, d.lat, d.lng);
      }
    },

    requestRide: async (p, d) => {
      const pickup = p || state.pickup;
      const dropoff = d || state.dropoff;
      dispatch({ type: 'SET_STATUS', payload: TRIP_STATUS.SEARCHING });
      notify('passenger', 'Looking for a driver near you…', 'info');

      // Simple fare estimation (dist * 10000 VND)
      const dist = Math.sqrt(Math.pow(pickup.lat - dropoff.lat, 2) + Math.pow(pickup.lng - dropoff.lng, 2));
      const estimatedPrice = Math.max(30000, Math.round(dist * 800000) / 10 * 10); 

      const payload = {
        source_lat:      pickup.lat,
        source_lng:      pickup.lng,
        dest_lat:        dropoff.lat,
        dest_lng:        dropoff.lng,
        estimated_price: estimatedPrice,
      };

      const headers = { 'X-User-ID': 'c10a8c2f-3d6a-491c-acbb-d313cd4d625f' }; // Demo UUID

      try {
        const res = await axios.post(`${API.ride}/v1/trips`, payload, { headers });
        const tripId = res.data?.trip_id ?? res.data?.id ?? `TRIP-${Date.now()}`;
        dispatch({ type: 'SET_TRIP_ID', payload: tripId });
        logApi('POST', '/v1/trips', payload, res.status, res.data);
        
        // Note: We don't pushEvent here anymore because we'll get it from the WebSocket
        // notify('passenger', 'Trip request sent. Matching you with a driver…', 'success');
        
        return tripId;

      } catch (err) {
        logApi('POST', '/v1/trips', payload, err.response?.status ?? 0, err.message);
        // Offline mode: simulate anyway
        const tripId = `TRIP-${Date.now()}`;
        dispatch({ type: 'SET_TRIP_ID', payload: tripId });
        pushEvent('Trip.Requested', 'ride', { tripId, pickup: pickup.label, dropoff: dropoff.label, offline: true });
        
        await new Promise(r => setTimeout(r, 1200));
        
        // Match Nearest Driver (Offline)
        const drivers = state.drivers.length > 0 ? state.drivers : DRIVER_SEEDS;
        const driver = drivers.reduce((prev, curr) => {
          const dPrev = Math.pow(prev.lat - pickup.lat, 2) + Math.pow(prev.lng - pickup.lng, 2);
          const dCurr = Math.pow(curr.lat - pickup.lat, 2) + Math.pow(curr.lng - pickup.lng, 2);
          return dCurr < dPrev ? curr : prev;
        });

        dispatch({ type: 'ASSIGN_DRIVER', payload: driver });
        pushEvent('Trip.Matched', 'dispatch', { tripId, driverId: driver.id, driverName: driver.name, offline: true });
        notify('passenger', `[Offline] Driver matched: ${driver.name}`, 'success');
        notify('driver', `[Offline] New ride! Go to ${pickup.label}.`, 'info');
        return tripId;
      }
    },

    acceptTrip: async (tripId, driverId) => {
      if (!tripId) return;
      const dId = driverId || state.assignedDriver?.id;
      const payload = { driver_id: dId };
      try {
        const res = await axios.post(`${API.ride}/v1/trips/${tripId}/accept`, payload);
        logApi('POST', `/v1/trips/${tripId}/accept`, payload, res.status, res.data);
      } catch (err) {
        logApi('POST', `/v1/trips/${tripId}/accept`, payload, err.response?.status ?? 0, err.message);
      }
      dispatch({ type: 'SET_STATUS', payload: TRIP_STATUS.ACCEPTED });
      pushEvent('Trip.Accepted', 'ride', { tripId });
      notify('passenger', 'Driver accepted the trip!', 'success');
      notify('driver', 'Trip accepted. Heading to pickup.', 'info');
      
      // Auto-start movement to pickup
      actions.moveToPickup(state.assignedDriver || DRIVER_SEEDS[0], state.pickup, state.speed);
    },

    startTrip: async (tripId) => {
      if (!tripId) return;
      try {
        const res = await axios.post(`${API.ride}/v1/trips/${tripId}/start`);
        logApi('POST', `/v1/trips/${tripId}/start`, {}, res.status, res.data);
      } catch (err) {
        logApi('POST', `/v1/trips/${tripId}/start`, {}, err.response?.status ?? 0, err.message);
      }
      dispatch({ type: 'SET_STATUS', payload: TRIP_STATUS.ON_TRIP });
      pushEvent('Trip.Started', 'ride', { tripId });
      notify('passenger', 'Trip started!', 'success');
      
      // Auto-start movement to destination
      actions.moveToDestination(state.assignedDriver, state.pickup, state.dropoff, state.speed);
    },

    cancelTrip: async (tripId, reason = 'Cancelled by user') => {
      if (!tripId) return;
      const payload = { reason };
      try {
        const res = await axios.post(`${API.ride}/v1/trips/${tripId}/cancel`, payload);
        logApi('POST', `/v1/trips/${tripId}/cancel`, payload, res.status, res.data);
      } catch (err) {
        logApi('POST', `/v1/trips/${tripId}/cancel`, payload, err.response?.status ?? 0, err.message);
      }
      dispatch({ type: 'SET_STATUS', payload: TRIP_STATUS.CANCELLED });
      pushEvent('Trip.Cancelled', 'ride', { tripId, reason });
      notify('passenger', 'Trip cancelled.', 'warning');
    },

    moveToPickup: async (driver, pickup, speed = 1) => {
      if (!driver) return;
      dispatch({ type: 'SET_DRIVER_MOVING', payload: true });
      
      await moveBetween(driver, pickup, 12, speed, (lat, lng) => {
        dispatch({ type: 'UPDATE_DRIVER_POS', payload: { id: driver.id, lat, lng } });
        sendLocationUpdate(driver.id, lat, lng);
      });

      dispatch({ type: 'SET_DRIVER_MOVING', payload: false });
      notify('driver', 'You have arrived at the pickup location.', 'success');
      notify('passenger', 'Your driver has arrived!', 'info');
    },

    moveToDestination: async (driver, pickup, dropoff, speed = 1) => {
      if (!driver) return;
      dispatch({ type: 'SET_DRIVER_MOVING', payload: true });

      await moveBetween(pickup, dropoff, 16, speed, (lat, lng) => {
        dispatch({ type: 'UPDATE_DRIVER_POS', payload: { id: driver.id, lat, lng } });
        sendLocationUpdate(driver.id, lat, lng);
      });

      dispatch({ type: 'SET_DRIVER_MOVING', payload: false });
      notify('driver', 'You have arrived at the destination.', 'success');
    },

    completeTrip: async (tripId) => {
      if (!tripId) return;
      // Use estimated price + small random variance for final price
      const finalPrice = 45000; // Hardcoded or calculated
      const payload = { final_price: finalPrice };
      
      try {
        const res = await axios.post(`${API.ride}/v1/trips/${tripId}/complete`, payload);
        logApi('POST', `/v1/trips/${tripId}/complete`, payload, res.status, res.data);
      } catch (err) {
        logApi('POST', `/v1/trips/${tripId}/complete`, payload, err.response?.status ?? 0, err.message);
      }
      dispatch({ type: 'SET_STATUS', payload: TRIP_STATUS.COMPLETED });
      pushEvent('Trip.Completed', 'ride', { tripId });
      notify('passenger', '🎉 Trip completed! Thanks for riding with Vroom.', 'success');
      notify('driver', '✅ Trip completed. Great job!', 'success');
    },

    reset: () => {
      dispatch({ type: 'RESET' });
      pushEvent('Demo.Reset', 'system', {});
    },

    setPickup:  (loc) => dispatch({ type: 'SET_PICKUP',  payload: loc }),
    setDropoff: (loc) => dispatch({ type: 'SET_DROPOFF', payload: loc }),
    setAutoPlay:(v)   => dispatch({ type: 'SET_AUTO_PLAY', payload: v }),
    setSpeed:   (v)   => dispatch({ type: 'SET_SPEED',  payload: v }),
    setStepMode:(v)   => dispatch({ type: 'SET_STEP_MODE', payload: v }),
    dismissNotif:(id) => dispatch({ type: 'DISMISS_NOTIFICATION', payload: id }),
  };

  return (
    <StoreCtx.Provider value={{ state, actions, pushEvent, notify }}>
      {children}
    </StoreCtx.Provider>
  );
}

export function useDemo() {
  const ctx = useContext(StoreCtx);
  if (!ctx) throw new Error('useDemo must be used inside DemoStoreProvider');
  return ctx;
}

/* ── Utility: animate movement between two lat/lng points ── */
async function moveBetween(from, to, steps, speed, onStep) {
  const delay = Math.max(100, 350 / speed);
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    const lat = from.lat + (to.lat - from.lat) * t;
    const lng = from.lng + (to.lng - from.lng) * t;
    onStep(lat, lng);
    await new Promise(r => setTimeout(r, delay));
  }
}
