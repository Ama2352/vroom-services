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
};

/* ── Trip lifecycle states ── */
export const TRIP_STATUS = {
  IDLE:       'Idle',
  SEARCHING:  'Searching Driver',
  ASSIGNED:   'Driver Assigned',
  COMING:     'Driver Coming',
  ON_TRIP:    'On Trip',
  COMPLETED:  'Completed',
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
    const id = `notif-${Date.now()}`;
    dispatch({ type: 'PUSH_NOTIFICATION', payload: { id, side, message, variant, ts: new Date() } });
    setTimeout(() => dispatch({ type: 'DISMISS_NOTIFICATION', payload: id }), 5000);
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

    requestRide: async (pickup, dropoff) => {
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
        pushEvent('Trip.Requested', 'ride', { tripId, pickup: pickup.label, dropoff: dropoff.label });
        notify('passenger', 'Trip request sent. Matching you with a driver…', 'success');

        // Simulate backend dispatch match after short delay
        await new Promise(r => setTimeout(r, 1500));
        
        // Match Nearest Driver
        const drivers = state.drivers.length > 0 ? state.drivers : DRIVER_SEEDS;
        const driver = drivers.reduce((prev, curr) => {
          const dPrev = Math.pow(prev.lat - pickup.lat, 2) + Math.pow(prev.lng - pickup.lng, 2);
          const dCurr = Math.pow(curr.lat - pickup.lat, 2) + Math.pow(curr.lng - pickup.lng, 2);
          return dCurr < dPrev ? curr : prev;
        });

        dispatch({ type: 'ASSIGN_DRIVER', payload: driver });
        pushEvent('Trip.Matched', 'dispatch', { tripId, driverId: driver.id, driverName: driver.name });
        notify('passenger', `Driver matched: ${driver.name} is on the way!`, 'success');
        notify('driver', `New ride request! Heading to ${pickup.label}.`, 'info');
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
      dispatch({ type: 'SET_STATUS', payload: TRIP_STATUS.COMING });
      pushEvent('Trip.Accepted', 'ride', { tripId });
      notify('passenger', 'Driver accepted the trip and is heading to you!', 'success');
      notify('driver', 'Trip accepted. Navigate to pickup point.', 'info');
    },

    simulateMovement: async (driver, pickup, dropoff, onTick, speed = 1) => {
      if (!driver) return;
      dispatch({ type: 'SET_DRIVER_MOVING', payload: true });

      // Step 1: Driver → Pickup
      await moveBetween(driver, pickup, 12, speed, (lat, lng) => {
        dispatch({ type: 'UPDATE_DRIVER_POS', payload: { id: driver.id, lat, lng } });
        sendLocationUpdate(driver.id, lat, lng); // Streaming location via WS
        onTick?.({ id: driver.id, lat, lng });
      });

      dispatch({ type: 'SET_STATUS', payload: TRIP_STATUS.ON_TRIP });
      pushEvent('Trip.PickedUp', 'ride', { driverId: driver.id });
      notify('passenger', 'Driver has arrived! Enjoy your trip 🚗', 'success');
      notify('driver', 'Passenger on board. Navigate to destination.', 'info');

      // Step 2: Pickup → Dropoff
      const pickupPos = { lat: pickup.lat, lng: pickup.lng };
      await moveBetween(pickupPos, dropoff, 16, speed, (lat, lng) => {
        dispatch({ type: 'UPDATE_DRIVER_POS', payload: { id: driver.id, lat, lng } });
        sendLocationUpdate(driver.id, lat, lng); // Streaming location via WS
        onTick?.({ id: driver.id, lat, lng });
      });

      dispatch({ type: 'SET_DRIVER_MOVING', payload: false });
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
