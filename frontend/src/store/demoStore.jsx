/**
 * demoStore.jsx – Centralized state for the Vroom demo UI.
 *
 * Principles:
 *  - No offline fallback: all state transitions come from the backend
 *  - WS events are the single source of truth for trip state
 *  - API actions (accept/start/complete) only make HTTP calls; they never
 *    mutate trip state themselves — the resulting WS event does
 *  - Searching has a hard 20-second timeout that surfaces a clear error
 *  - Reset auto-reseeds drivers so the backend is immediately ready
 */
import {
  createContext,
  useContext,
  useReducer,
  useCallback,
  useRef,
  useEffect,
} from "react";
import axios from "axios";

/* ─────────────────────────────────────────────
   API Configuration (local dev vs cluster)
───────────────────────────────────────────── */
const isLocal =
  window.location.hostname === "localhost" ||
  window.location.hostname === "127.0.0.1";
const host = window.location.host;
const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
const VAGRANT_IP = "192.168.242.10";

export const API = {
  user:     isLocal ? `http://${VAGRANT_IP}/user-service`         : "/user-service",
  ride:     isLocal ? `http://${VAGRANT_IP}/ride-service`         : "/ride-service",
  dispatch: isLocal ? `http://${VAGRANT_IP}/dispatch-service`     : "/dispatch-service",
  notification: isLocal ? `http://${VAGRANT_IP}/notification-service` : "/notification-service",
  dispatchWS: isLocal
    ? `ws://${VAGRANT_IP}/dispatch-service/v1/dispatch/ws/location`
    : `${wsProtocol}//${host}/dispatch-service/v1/dispatch/ws/location`,
  notificationWS: isLocal
    ? `ws://${VAGRANT_IP}/notification-service/v1/ws`
    : `${wsProtocol}//${host}/notification-service/v1/ws`,
};

/* ─────────────────────────────────────────────
   Constants
───────────────────────────────────────────── */
export const TRIP_STATUS = {
  IDLE:      "Idle",
  SEARCHING: "Searching Driver",
  ASSIGNED:  "Driver Assigned",
  ACCEPTED:  "Accepted",
  COMING:    "Driver Coming",
  ON_TRIP:   "On Trip",
  COMPLETED: "Completed",
  CANCELLED: "Cancelled",
};

export const HCMC_CENTER = [10.7769, 106.7009];

export const DRIVER_SEEDS = [
  { id: "550e8400-e29b-41d4-a716-446655440001", name: "Nguyen Van An",  lat: 10.780,  lng: 106.697, avatar: "🧑" },
  { id: "550e8400-e29b-41d4-a716-446655440002", name: "Tran Thi Bich",  lat: 10.775,  lng: 106.705, avatar: "👩" },
  { id: "550e8400-e29b-41d4-a716-446655440003", name: "Le Van Cuong",   lat: 10.782,  lng: 106.708, avatar: "👨" },
];

export const PICKUP_PRESETS = [
  { label: "Ben Thanh Market",  lat: 10.7724, lng: 106.6980 },
  { label: "Nguyen Hue Walk",   lat: 10.7754, lng: 106.7028 },
  { label: "Bitexco Tower",     lat: 10.7718, lng: 106.7040 },
];

export const DROPOFF_PRESETS = [
  { label: "Tan Son Nhat Airport", lat: 10.8184, lng: 106.6697 },
  { label: "Landmark 81",          lat: 10.7947, lng: 106.7218 },
  { label: "Saigon Zoo",           lat: 10.7895, lng: 106.7052 },
];

const DEMO_PASSENGER_ID = "c10a8c2f-3d6a-491c-acbb-d313cd4d625f";

// How long to wait for Trip.Matched before giving up
const SEARCH_TIMEOUT_MS = 20_000;

/* ─────────────────────────────────────────────
   Helpers
───────────────────────────────────────────── */
function calcMetrics(pickup, dropoff) {
  if (!pickup || !dropoff) return { fare: 0, time: 0 };
  const dLat = pickup.lat - dropoff.lat;
  const dLng = pickup.lng - dropoff.lng;
  const dist = Math.sqrt(dLat * dLat + dLng * dLng) * 111; // rough km
  const fare = Math.round((dist * 12000 + 15000) / 1000) * 1000;
  const time = Math.round(dist * 2.5 + 4);
  return { fare, time };
}

/* ─────────────────────────────────────────────
   State shape
───────────────────────────────────────────── */
const { fare: initFare, time: initTime } = calcMetrics(PICKUP_PRESETS[0], DROPOFF_PRESETS[0]);

const initialState = {
  tripStatus:    TRIP_STATUS.IDLE,
  tripId:        null,
  drivers:       [],
  assignedDriver: null,
  pickup:        PICKUP_PRESETS[0],
  dropoff:       DROPOFF_PRESETS[0],
  events:        [],
  notifications: [],
  apiLog:        null,
  driverMoving:  false,
  speed:         1,
  estimatedFare: initFare,
  estimatedTime: initTime,
  wsStatus:      "connecting", // 'connecting' | 'connected' | 'disconnected'
  error:         null,
  // kept for component compatibility
  autoPlay:  false,
  stepMode:  false,
};

/* ─────────────────────────────────────────────
   Reducer
───────────────────────────────────────────── */
function reducer(state, action) {
  switch (action.type) {
    case "SET_STATUS":
      return { ...state, tripStatus: action.payload };

    case "SET_TRIP_ID":
      return { ...state, tripId: action.payload };

    case "SEED_DRIVERS":
      return { ...state, drivers: DRIVER_SEEDS.map(d => ({ ...d })) };

    case "ASSIGN_DRIVER":
      return {
        ...state,
        assignedDriver: action.payload,
        tripStatus: TRIP_STATUS.ASSIGNED,
      };

    // Clear driver assignment and go back to searching (after offer rejection)
    case "CLEAR_DRIVER":
      return {
        ...state,
        assignedDriver: null,
        tripStatus: TRIP_STATUS.SEARCHING,
      };

    case "UPDATE_DRIVER_POS": {
      const updated = state.drivers.map(d =>
        d.id === action.payload.id
          ? { ...d, lat: action.payload.lat, lng: action.payload.lng }
          : d
      );
      const assigned =
        state.assignedDriver?.id === action.payload.id
          ? { ...state.assignedDriver, lat: action.payload.lat, lng: action.payload.lng }
          : state.assignedDriver;
      return { ...state, drivers: updated, assignedDriver: assigned };
    }

    case "SET_PICKUP": {
      const { fare, time } = calcMetrics(action.payload, state.dropoff);
      return { ...state, pickup: action.payload, estimatedFare: fare, estimatedTime: time };
    }

    case "SET_DROPOFF": {
      const { fare, time } = calcMetrics(state.pickup, action.payload);
      return { ...state, dropoff: action.payload, estimatedFare: fare, estimatedTime: time };
    }

    case "PUSH_EVENT":
      return { ...state, events: [action.payload, ...state.events].slice(0, 50) };

    case "PUSH_NOTIFICATION":
      return { ...state, notifications: [action.payload, ...state.notifications].slice(0, 30) };

    case "DISMISS_NOTIFICATION":
      return { ...state, notifications: state.notifications.filter(n => n.id !== action.payload) };

    case "SET_API_LOG":
      return { ...state, apiLog: action.payload };

    case "SET_DRIVER_MOVING":
      return { ...state, driverMoving: action.payload };

    case "SET_WS_STATUS":
      return { ...state, wsStatus: action.payload };

    case "SET_ERROR":
      return { ...state, error: action.payload };

    case "SET_SPEED":
      return { ...state, speed: action.payload };

    case "SET_AUTO_PLAY":
      return { ...state, autoPlay: action.payload };

    case "SET_STEP_MODE":
      return { ...state, stepMode: action.payload };

    case "RESET": {
      const { fare, time } = calcMetrics(state.pickup, state.dropoff);
      return {
        ...initialState,
        pickup:        state.pickup,
        dropoff:       state.dropoff,
        estimatedFare: fare,
        estimatedTime: time,
        wsStatus:      state.wsStatus, // keep WS connection status across resets
      };
    }

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

  // Refs that outlive renders
  const wsRef              = useRef(null);       // dispatch WebSocket
  const processedEventIds  = useRef(new Set());
  const searchTimeoutRef   = useRef(null);
  // Always-current state snapshot for use inside WS / async callbacks
  const stateRef           = useRef(state);

  useEffect(() => { stateRef.current = state; }, [state]);

  /* ── Dispatch WebSocket (driver location relay) ── */
  useEffect(() => {
    let dead = false;
    let retries = 0;
    const MAX = 5;

    const connect = () => {
      if (dead || retries >= MAX) return;
      const ws = new WebSocket(API.dispatchWS);
      ws.onopen  = () => { wsRef.current = ws; retries = 0; };
      ws.onclose = () => {
        wsRef.current = null;
        retries++;
        if (!dead && retries < MAX) setTimeout(connect, 5000);
      };
      ws.onerror = () => {};
    };

    connect();
    return () => {
      dead = true;
      wsRef.current?.close();
    };
  }, []);

  /* ── Stable helpers ── */
  const logApi = useCallback((method, url, payload, status, response) => {
    dispatch({
      type: "SET_API_LOG",
      payload: { method, url, payload, status, response, ts: new Date() },
    });
  }, []);

  const pushEvent = useCallback((type, service, detail = {}) => {
    dispatch({
      type: "PUSH_EVENT",
      payload: {
        id: `evt-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        type, service, detail, ts: new Date(),
      },
    });
  }, []);

  const notify = useCallback((side, message, variant = "info") => {
    const id = `notif-${Date.now()}-${Math.random()}`;
    dispatch({ type: "PUSH_NOTIFICATION", payload: { id, side, message, variant, ts: new Date() } });
    setTimeout(() => dispatch({ type: "DISMISS_NOTIFICATION", payload: id }), 12000);
  }, []);

  const sendLocationUpdate = useCallback((driverId, lat, lng) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ driver_id: driverId, lat, lng }));
    }
  }, []);

  /* ── Movement animations ── */
  const moveToPickup = useCallback(async (driver, pickup, speed = 1) => {
    if (!driver || !pickup) return;
    dispatch({ type: "SET_DRIVER_MOVING", payload: true });
    await moveBetween(driver, pickup, 12, speed, (lat, lng) => {
      dispatch({ type: "UPDATE_DRIVER_POS", payload: { id: driver.id, lat, lng } });
      sendLocationUpdate(driver.id, lat, lng);
    });
    dispatch({ type: "SET_DRIVER_MOVING", payload: false });
    notify("driver",    "Arrived at pickup location.", "success");
    notify("passenger", "Your driver has arrived!",    "info");
  }, [sendLocationUpdate, notify]);

  const moveToDestination = useCallback(async (driver, dropoff, speed = 1) => {
    if (!driver || !dropoff) return;
    dispatch({ type: "SET_DRIVER_MOVING", payload: true });
    await moveBetween(driver, dropoff, 16, speed, (lat, lng) => {
      dispatch({ type: "UPDATE_DRIVER_POS", payload: { id: driver.id, lat, lng } });
      sendLocationUpdate(driver.id, lat, lng);
    });
    dispatch({ type: "SET_DRIVER_MOVING", payload: false });
    notify("driver", "Arrived at destination.", "success");
  }, [sendLocationUpdate, notify]);

  /* ── Incoming WebSocket event handler ──
     Defined before the notification WS useEffect so the closure is clean.
     All dependencies here (dispatch, pushEvent, notify, moveToPickup,
     moveToDestination, stateRef, DRIVER_SEEDS) are stable across renders.
  ── */
  const handleIncomingEvent = useCallback((evt) => {
    const { event_type, payload } = evt;
    let data = {};
    try {
      data = typeof payload === "string" ? JSON.parse(payload) : (payload ?? {});
    } catch {
      data = payload ?? {};
    }

    console.log(`[WS EVENT] ${event_type}`, data);

    switch (event_type) {

      case "Trip.Requested":
        pushEvent("Trip.Requested", "ride", { tripId: data.id });
        break;

      case "Trip.Matched": {
        // A driver was found — cancel the no-match timeout
        if (searchTimeoutRef.current) {
          clearTimeout(searchTimeoutRef.current);
          searchTimeoutRef.current = null;
        }
        const driverId = data.driver_id;
        const matchedDriver =
          DRIVER_SEEDS.find(d => d.id === driverId) ?? DRIVER_SEEDS[0];
        dispatch({ type: "ASSIGN_DRIVER", payload: matchedDriver });
        pushEvent("Trip.Matched", "dispatch", {
          tripId: data.id,
          driverName: matchedDriver.name,
        });
        notify("passenger", `Driver matched: ${matchedDriver.name}`, "success");
        notify("driver",    "New ride offer! Review and accept.",      "info");
        break;
      }

      case "Trip.Accepted": {
        dispatch({ type: "SET_STATUS", payload: TRIP_STATUS.ACCEPTED });
        pushEvent("Trip.Accepted", "ride", { tripId: data.id });
        notify("passenger", "Driver accepted! Heading to your location.", "success");
        notify("driver",    "Trip accepted. Navigating to passenger.",     "info");
        // Start moving to pickup using the current driver position from stateRef
        const { assignedDriver, pickup, speed } = stateRef.current;
        if (assignedDriver && pickup) {
          moveToPickup(assignedDriver, pickup, speed);
        }
        break;
      }

      case "Trip.Started": {
        dispatch({ type: "SET_STATUS", payload: TRIP_STATUS.ON_TRIP });
        pushEvent("Trip.Started", "ride", { tripId: data.id });
        notify("passenger", "Trip started! Enjoy your ride.", "success");
        notify("driver",    "Trip in progress.",               "info");
        // Start moving to destination from wherever the driver currently is
        const driver  = stateRef.current.assignedDriver;
        const dropoff = stateRef.current.dropoff;
        const spd     = stateRef.current.speed;
        if (driver && dropoff) {
          moveToDestination(driver, dropoff, spd);
        }
        break;
      }

      case "Trip.OfferRejected":
        // Dispatch re-matches automatically — reset to searching and restart timeout
        dispatch({ type: "CLEAR_DRIVER" });
        pushEvent("Trip.OfferRejected", "dispatch", {
          tripId: data.id, driverId: data.driver_id,
        });
        notify("passenger", "Driver unavailable. Looking for another...", "info");
        notify("driver",    "Offer rejected.",                             "warning");
        // Restart the match timeout for the re-search cycle
        if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
        searchTimeoutRef.current = setTimeout(() => {
          searchTimeoutRef.current = null;
          if (stateRef.current.tripStatus === TRIP_STATUS.SEARCHING) {
            dispatch({ type: "SET_STATUS",  payload: TRIP_STATUS.IDLE });
            dispatch({ type: "SET_TRIP_ID", payload: null });
            notify("passenger", "No driver found. Please try again.", "warning");
            pushEvent("Trip.MatchTimeout", "system", {});
          }
        }, SEARCH_TIMEOUT_MS);
        break;

      case "Trip.MatchFailed":
        if (searchTimeoutRef.current) {
          clearTimeout(searchTimeoutRef.current);
          searchTimeoutRef.current = null;
        }
        dispatch({ type: "SET_STATUS",  payload: TRIP_STATUS.IDLE });
        dispatch({ type: "SET_TRIP_ID", payload: null });
        pushEvent("Trip.MatchFailed", "dispatch", { reason: data.reason });
        notify("passenger", "No drivers available nearby. Please try again.", "warning");
        break;

      case "Trip.Cancelled":
        if (searchTimeoutRef.current) {
          clearTimeout(searchTimeoutRef.current);
          searchTimeoutRef.current = null;
        }
        dispatch({ type: "SET_STATUS", payload: TRIP_STATUS.CANCELLED });
        pushEvent("Trip.Cancelled", "ride", { reason: data.reason });
        notify("passenger", "Trip has been cancelled.",          "warning");
        notify("driver",    "Trip cancelled.",                   "warning");
        break;

      case "Trip.Completed":
        dispatch({ type: "SET_STATUS", payload: TRIP_STATUS.COMPLETED });
        pushEvent("Trip.Completed", "ride", { tripId: data.id });
        notify("passenger", "Trip completed! Thanks for riding with Vroom.", "success");
        notify("driver",    "Trip completed. Great job!",                     "success");
        break;

      default:
        pushEvent(event_type, "system", data);
    }
  }, [dispatch, pushEvent, notify, moveToPickup, moveToDestination]);

  /* ── Notification WebSocket (trip event channel) ── */
  useEffect(() => {
    let dead = false;

    const connect = () => {
      if (dead) return;
      dispatch({ type: "SET_WS_STATUS", payload: "connecting" });
      const ws = new WebSocket(`${API.notificationWS}?userId=${DEMO_PASSENGER_ID}`);

      ws.onopen = () => {
        dispatch({ type: "SET_WS_STATUS", payload: "connected" });
        console.log("✅ Notification WS connected");
      };

      ws.onmessage = (event) => {
        try {
          const data = typeof event.data === "string"
            ? JSON.parse(event.data)
            : event.data;

          // Deduplicate using event id or a content-based fallback key
          const uid =
            data.id ??
            data.correlation_id ??
            `${data.event_type}-${JSON.stringify(data.payload)}`;

          if (processedEventIds.current.has(uid)) return;
          processedEventIds.current.add(uid);

          // Cap the dedup set to avoid unbounded growth
          if (processedEventIds.current.size > 200) {
            processedEventIds.current.delete(
              processedEventIds.current.values().next().value
            );
          }

          handleIncomingEvent(data);
        } catch (err) {
          console.error("[WS] Message parse error:", err);
        }
      };

      ws.onclose = () => {
        if (!dead) {
          dispatch({ type: "SET_WS_STATUS", payload: "disconnected" });
          console.warn("⚠️ Notification WS closed, reconnecting in 3s...");
          setTimeout(connect, 3000);
        }
      };

      ws.onerror = () => {};
    };

    connect();
    return () => { dead = true; };
  }, [handleIncomingEvent]);

  /* ── Driver location heartbeat ──
     Keeps driver_last_seen TTL alive in the dispatch service (30s TTL,
     refreshed every 5s).  Without this, dispatch stops seeing drivers as
     "fresh" and refuses to match them.
  ── */
  const driversRef = useRef(state.drivers);
  useEffect(() => { driversRef.current = state.drivers; }, [state.drivers]);

  useEffect(() => {
    if (state.drivers.length === 0) return;
    const interval = setInterval(() => {
      driversRef.current.forEach(d => {
        axios
          .put(`${API.dispatch}/v1/drivers/${d.id}/location`, { lat: d.lat, lng: d.lng })
          .catch(() => {});
        sendLocationUpdate(d.id, d.lat, d.lng);
      });
    }, 5000);
    return () => clearInterval(interval);
  }, [state.drivers.length, sendLocationUpdate]);

  /* ─────────────────────────────────────────────
     Actions
  ───────────────────────────────────────────── */
  const actions = {

    seedDrivers: async () => {
      dispatch({ type: "SEED_DRIVERS" });

      for (const d of DRIVER_SEEDS) {
        const payload = { lat: d.lat, lng: d.lng };
        try {
          const res = await axios.put(
            `${API.dispatch}/v1/drivers/${d.id}/location`, payload
          );
          logApi("PUT", `/v1/drivers/${d.id}/location`, payload, res.status, res.data);
        } catch (err) {
          logApi("PUT", `/v1/drivers/${d.id}/location`, payload,
            err.response?.status ?? 0, err.message);
        }
        sendLocationUpdate(d.id, d.lat, d.lng);
      }

      pushEvent("Drivers.Seeded", "dispatch", { count: DRIVER_SEEDS.length });
      notify("driver", "3 drivers are now online and ready.", "success");
    },

    requestRide: async (p, d) => {
      const pickup  = p ?? stateRef.current.pickup;
      const dropoff = d ?? stateRef.current.dropoff;

      if (stateRef.current.drivers.length === 0) {
        notify("passenger", "Please seed drivers first.", "warning");
        return;
      }

      dispatch({ type: "SET_STATUS", payload: TRIP_STATUS.SEARCHING });
      dispatch({ type: "SET_ERROR",  payload: null });
      notify("passenger", "Looking for a driver near you...", "info");

      const dist = Math.sqrt(
        (pickup.lat - dropoff.lat) ** 2 + (pickup.lng - dropoff.lng) ** 2
      );
      const estimatedPrice = Math.max(30000, Math.round(dist * 800000 / 1000) * 1000);

      const payload = {
        source_lat:      pickup.lat,
        source_lng:      pickup.lng,
        dest_lat:        dropoff.lat,
        dest_lng:        dropoff.lng,
        estimated_price: estimatedPrice,
        currency:        "VND",
      };

      try {
        const res = await axios.post(`${API.ride}/v1/trips`, payload, {
          headers: { "X-User-ID": DEMO_PASSENGER_ID },
        });
        const tripId = res.data?.trip_id ?? res.data?.id;
        dispatch({ type: "SET_TRIP_ID", payload: tripId });
        logApi("POST", "/v1/trips", payload, res.status, res.data);
        pushEvent("Trip.Requested", "ride", { tripId, pickup: pickup.label, dropoff: dropoff.label });

        // Start the no-match timeout — cleared when Trip.Matched (or MatchFailed) arrives
        if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
        searchTimeoutRef.current = setTimeout(() => {
          searchTimeoutRef.current = null;
          if (stateRef.current.tripStatus === TRIP_STATUS.SEARCHING) {
            dispatch({ type: "SET_STATUS",  payload: TRIP_STATUS.IDLE });
            dispatch({ type: "SET_TRIP_ID", payload: null });
            pushEvent("Trip.MatchTimeout", "system", { tripId, pickup: pickup.label });
            notify("passenger",
              "No driver found nearby after 20 seconds. Please try again or re-seed drivers.",
              "warning");
          }
        }, SEARCH_TIMEOUT_MS);

      } catch (err) {
        const msg = err.response?.data?.error ?? err.message ?? "Request failed";
        logApi("POST", "/v1/trips", payload, err.response?.status ?? 0, err.message);
        dispatch({ type: "SET_STATUS", payload: TRIP_STATUS.IDLE });
        dispatch({ type: "SET_ERROR",  payload: msg });
        notify("passenger", `Could not create ride: ${msg}`, "warning");
        pushEvent("Trip.RequestError", "ride", { error: msg });
      }
    },

    // Accept and reject only make the API call.
    // The state transition is driven by the resulting WS event (Trip.Accepted / Trip.OfferRejected).
    acceptTrip: async (tripId, driverId) => {
      if (!tripId) return;
      const dId = driverId ?? stateRef.current.assignedDriver?.id;
      if (!dId) {
        notify("driver", "No driver ID available to accept.", "warning");
        return;
      }
      const payload = { driver_id: dId };
      try {
        const res = await axios.post(`${API.ride}/v1/trips/${tripId}/accept`, payload);
        logApi("POST", `/v1/trips/${tripId}/accept`, payload, res.status, res.data);
      } catch (err) {
        logApi("POST", `/v1/trips/${tripId}/accept`, payload,
          err.response?.status ?? 0, err.message);
        const msg = err.response?.data?.error ?? err.message;
        notify("driver", `Accept failed: ${msg}`, "warning");
      }
    },

    rejectOffer: async (tripId, driverId) => {
      if (!tripId || !driverId) return;
      const payload = { driver_id: driverId };
      try {
        const res = await axios.post(`${API.ride}/v1/trips/${tripId}/reject`, payload);
        logApi("POST", `/v1/trips/${tripId}/reject`, payload, res.status, res.data);
        // State driven by Trip.OfferRejected WS event
      } catch (err) {
        logApi("POST", `/v1/trips/${tripId}/reject`, payload,
          err.response?.status ?? 0, err.message);
        const msg = err.response?.data?.error ?? err.message;
        notify("driver", `Reject failed: ${msg}`, "warning");
      }
    },

    startTrip: async (tripId) => {
      if (!tripId) return;
      try {
        const res = await axios.post(`${API.ride}/v1/trips/${tripId}/start`);
        logApi("POST", `/v1/trips/${tripId}/start`, {}, res.status, res.data);
        // State driven by Trip.Started WS event
      } catch (err) {
        logApi("POST", `/v1/trips/${tripId}/start`, {},
          err.response?.status ?? 0, err.message);
        const msg = err.response?.data?.error ?? err.message;
        notify("driver", `Start failed: ${msg}`, "warning");
      }
    },

    completeTrip: async (tripId) => {
      if (!tripId) return;
      // Use the dynamically calculated fare, not a hardcoded value
      const finalPrice = stateRef.current.estimatedFare;
      const payload = { final_price: finalPrice };
      try {
        const res = await axios.post(`${API.ride}/v1/trips/${tripId}/complete`, payload);
        logApi("POST", `/v1/trips/${tripId}/complete`, payload, res.status, res.data);
        // State driven by Trip.Completed WS event
      } catch (err) {
        logApi("POST", `/v1/trips/${tripId}/complete`, payload,
          err.response?.status ?? 0, err.message);
        const msg = err.response?.data?.error ?? err.message;
        notify("driver", `Complete failed: ${msg}`, "warning");
      }
    },

    cancelTrip: async (tripId, reason = "Cancelled by user") => {
      if (!tripId) return;
      if (searchTimeoutRef.current) {
        clearTimeout(searchTimeoutRef.current);
        searchTimeoutRef.current = null;
      }
      const payload = { reason };
      try {
        const res = await axios.post(`${API.ride}/v1/trips/${tripId}/cancel`, payload);
        logApi("POST", `/v1/trips/${tripId}/cancel`, payload, res.status, res.data);
        // State driven by Trip.Cancelled WS event
      } catch (err) {
        logApi("POST", `/v1/trips/${tripId}/cancel`, payload,
          err.response?.status ?? 0, err.message);
        // Cancellation is user-initiated — optimistically reflect it if the API call fails
        dispatch({ type: "SET_STATUS", payload: TRIP_STATUS.CANCELLED });
        notify("passenger", "Trip cancelled.", "warning");
      }
    },

    reset: async () => {
      if (searchTimeoutRef.current) {
        clearTimeout(searchTimeoutRef.current);
        searchTimeoutRef.current = null;
      }
      try {
        await Promise.all([
          axios.post(`${API.ride}/v1/debug/reset`),
          axios.post(`${API.dispatch}/v1/debug/reset`),
        ]);
        processedEventIds.current.clear();
        dispatch({ type: "RESET" });
        pushEvent("System.Reset", "system", { success: true });
        // After backend reset (FlushDB on dispatch), drivers are gone from Redis.
        // Auto-reseed so the next ride request finds available drivers immediately.
        setTimeout(() => actions.seedDrivers(), 300);
      } catch (err) {
        const msg = err.response?.data?.error ?? err.message;
        notify("passenger", `Reset failed: ${msg}. Check backend connectivity.`, "warning");
        pushEvent("System.ResetError", "system", { error: msg });
      }
    },

    setPickup:    (loc) => dispatch({ type: "SET_PICKUP",    payload: loc }),
    setDropoff:   (loc) => dispatch({ type: "SET_DROPOFF",   payload: loc }),
    setSpeed:     (v)   => dispatch({ type: "SET_SPEED",     payload: v }),
    setAutoPlay:  (v)   => dispatch({ type: "SET_AUTO_PLAY", payload: v }),
    setStepMode:  (v)   => dispatch({ type: "SET_STEP_MODE", payload: v }),
    dismissNotif: (id)  => dispatch({ type: "DISMISS_NOTIFICATION", payload: id }),
    dismissError: ()    => dispatch({ type: "SET_ERROR", payload: null }),
  };

  return (
    <StoreCtx.Provider value={{ state, actions, pushEvent, notify }}>
      {children}
    </StoreCtx.Provider>
  );
}

export function useDemo() {
  const ctx = useContext(StoreCtx);
  if (!ctx) throw new Error("useDemo must be used inside DemoStoreProvider");
  return ctx;
}

/* ─────────────────────────────────────────────
   Utility: animate between two lat/lng points
───────────────────────────────────────────── */
async function moveBetween(from, to, steps, speed, onStep) {
  const delay = Math.max(100, 350 / speed);
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    onStep(
      from.lat + (to.lat - from.lat) * t,
      from.lng + (to.lng - from.lng) * t,
    );
    await new Promise(r => setTimeout(r, delay));
  }
}
