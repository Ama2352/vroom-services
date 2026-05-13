/**
 * demoStore.jsx – Ground-up rebuild of the Vroom demo state layer.
 *
 * Architecture:
 *  - Polling (GET /v1/trips/:id every 2s) is the source of truth for trip state.
 *  - Optimistic updates: every action immediately reflects the expected outcome
 *    in local state so the UI is always snappy, even before the poll confirms.
 *  - WebSocket is used for real-time toast notifications and animation triggers.
 *    If WS is unavailable the app still functions correctly via polling.
 *  - No offline fallback. If the backend is unreachable the user sees an error.
 *  - Animations (driver movement) are triggered by state transitions and
 *    de-duplicated with refs so they fire exactly once per trip leg.
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
   API base URLs
───────────────────────────────────────────── */
const isLocal =
  window.location.hostname === "localhost" ||
  window.location.hostname === "127.0.0.1";
const host = window.location.host;
const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
const VAGRANT_IP = "192.168.25.136";

export const API = {
  ride: isLocal ? `http://${VAGRANT_IP}/ride-service` : "/ride-service",
  dispatch: isLocal
    ? `http://${VAGRANT_IP}/dispatch-service`
    : "/dispatch-service",
  notificationWS: isLocal
    ? `ws://${VAGRANT_IP}/notification-service/v1/ws`
    : `${wsProtocol}//${host}/notification-service/v1/ws`,
  dispatchWS: isLocal
    ? `ws://${VAGRANT_IP}/dispatch-service/v1/dispatch/ws/location`
    : `${wsProtocol}//${host}/dispatch-service/v1/dispatch/ws/location`,
};

/* ─────────────────────────────────────────────
   Trip status constants
───────────────────────────────────────────── */
export const TRIP_STATUS = {
  IDLE: "Idle",
  SEARCHING: "Searching Driver",
  ASSIGNED: "Driver Assigned",
  ACCEPTED: "Accepted",
  COMING: "Driver Coming",
  ON_TRIP: "On Trip",
  COMPLETED: "Completed",
  CANCELLED: "Cancelled",
};

/** Map DB status + driver presence → frontend TRIP_STATUS */
function dbStatusToUI(dbStatus, hasDriver) {
  switch (dbStatus) {
    case "REQUESTED":
      return hasDriver ? TRIP_STATUS.ASSIGNED : TRIP_STATUS.SEARCHING;
    case "ACCEPTED":
      return TRIP_STATUS.ACCEPTED;
    case "IN_PROGRESS":
      return TRIP_STATUS.ON_TRIP;
    case "COMPLETED":
      return TRIP_STATUS.COMPLETED;
    case "CANCELLED":
      return TRIP_STATUS.CANCELLED;
    default:
      return TRIP_STATUS.SEARCHING;
  }
}

/* ─────────────────────────────────────────────
   Demo data constants
───────────────────────────────────────────── */
export const HCMC_CENTER = [10.7769, 106.7009];

export const DRIVER_SEEDS = [
  {
    id: "550e8400-e29b-41d4-a716-446655440001",
    name: "Nguyen Van An",
    lat: 10.78,
    lng: 106.697,
    avatar: "🧑",
  },
  {
    id: "550e8400-e29b-41d4-a716-446655440002",
    name: "Tran Thi Bich",
    lat: 10.775,
    lng: 106.705,
    avatar: "👩",
  },
  {
    id: "550e8400-e29b-41d4-a716-446655440003",
    name: "Le Van Cuong",
    lat: 10.782,
    lng: 106.708,
    avatar: "👨",
  },
];

export const PICKUP_PRESETS = [
  { label: "Ben Thanh Market", lat: 10.7724, lng: 106.698 },
  { label: "Nguyen Hue Walk", lat: 10.7754, lng: 106.7028 },
  { label: "Bitexco Tower", lat: 10.7718, lng: 106.704 },
];

export const DROPOFF_PRESETS = [
  { label: "Tan Son Nhat Airport", lat: 10.8184, lng: 106.6697 },
  { label: "Landmark 81", lat: 10.7947, lng: 106.7218 },
  { label: "Saigon Zoo", lat: 10.7895, lng: 106.7052 },
];

const DEMO_PASSENGER_ID = "c10a8c2f-3d6a-491c-acbb-d313cd4d625f";
const SEARCH_TIMEOUT_MS = 20_000; // give up if no driver matched in 20s

/* ─────────────────────────────────────────────
   Fare estimation helper
───────────────────────────────────────────── */
function calcMetrics(pickup, dropoff) {
  if (!pickup || !dropoff) return { fare: 0, time: 0 };
  const dLat = pickup.lat - dropoff.lat;
  const dLng = pickup.lng - dropoff.lng;
  const dist = Math.sqrt(dLat * dLat + dLng * dLng) * 111;
  const fare = Math.round((dist * 12000 + 15000) / 1000) * 1000;
  const time = Math.round(dist * 2.5 + 4);
  return { fare, time };
}

/* ─────────────────────────────────────────────
   Initial state
───────────────────────────────────────────── */
const { fare: _f, time: _t } = calcMetrics(
  PICKUP_PRESETS[0],
  DROPOFF_PRESETS[0],
);

const initialState = {
  tripStatus: TRIP_STATUS.IDLE,
  tripId: null,
  drivers: [],
  assignedDriver: null,
  pickup: PICKUP_PRESETS[0],
  dropoff: DROPOFF_PRESETS[0],
  events: [],
  notifications: [],
  apiLog: null,
  driverMoving: false,
  speed: 1,
  estimatedFare: _f,
  estimatedTime: _t,
  wsStatus: "connecting",
  error: null,
  // kept for component compat
  autoPlay: false,
  stepMode: false,
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
      return { ...state, drivers: DRIVER_SEEDS.map((d) => ({ ...d })) };

    // Full driver assign + set status to ASSIGNED (used by WS Trip.Matched)
    case "ASSIGN_DRIVER":
      return {
        ...state,
        assignedDriver: action.payload,
        tripStatus: TRIP_STATUS.ASSIGNED,
      };

    // Driver assignment only – status managed separately (used by poll sync)
    case "SET_DRIVER":
      return { ...state, assignedDriver: action.payload };

    // Clear driver + return to SEARCHING (used by WS Trip.OfferRejected)
    case "CLEAR_DRIVER":
      return {
        ...state,
        assignedDriver: null,
        tripStatus: TRIP_STATUS.SEARCHING,
      };

    case "UPDATE_DRIVER_POS": {
      const updated = state.drivers.map((d) =>
        d.id === action.payload.id
          ? { ...d, lat: action.payload.lat, lng: action.payload.lng }
          : d,
      );
      const assigned =
        state.assignedDriver?.id === action.payload.id
          ? {
              ...state.assignedDriver,
              lat: action.payload.lat,
              lng: action.payload.lng,
            }
          : state.assignedDriver;
      return { ...state, drivers: updated, assignedDriver: assigned };
    }

    case "SET_PICKUP": {
      const { fare, time } = calcMetrics(action.payload, state.dropoff);
      return {
        ...state,
        pickup: action.payload,
        estimatedFare: fare,
        estimatedTime: time,
      };
    }

    case "SET_DROPOFF": {
      const { fare, time } = calcMetrics(state.pickup, action.payload);
      return {
        ...state,
        dropoff: action.payload,
        estimatedFare: fare,
        estimatedTime: time,
      };
    }

    case "PUSH_EVENT":
      return {
        ...state,
        events: [action.payload, ...state.events].slice(0, 50),
      };

    case "PUSH_NOTIFICATION":
      return {
        ...state,
        notifications: [action.payload, ...state.notifications].slice(0, 30),
      };

    case "DISMISS_NOTIFICATION":
      return {
        ...state,
        notifications: state.notifications.filter(
          (n) => n.id !== action.payload,
        ),
      };

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
        pickup: state.pickup,
        dropoff: state.dropoff,
        estimatedFare: fare,
        estimatedTime: time,
        drivers: state.drivers, // keep seeded drivers on map
        wsStatus: state.wsStatus, // keep WS connection status
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

  // Stable refs
  const wsRef = useRef(null); // dispatch WS (location relay)
  const processedEventIds = useRef(new Set());
  const searchTimeoutRef = useRef(null);
  const stateRef = useRef(state); // always-current state snapshot
  const tripAnimRef = useRef({
    // per-trip animation guard
    tripId: null,
    pickupStarted: false,
    dropoffStarted: false,
  });
  const driversRef = useRef(state.drivers);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);
  useEffect(() => {
    driversRef.current = state.drivers;
  }, [state.drivers]);

  /* ── Dispatch WebSocket (driver location relay) ── */
  useEffect(() => {
    let dead = false;
    let retries = 0;

    const connect = () => {
      if (dead || retries >= 5) return;
      const ws = new WebSocket(API.dispatchWS);
      ws.onopen = () => {
        wsRef.current = ws;
        retries = 0;
      };
      ws.onclose = () => {
        wsRef.current = null;
        retries++;
        if (!dead && retries < 5) setTimeout(connect, 5000);
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
        type,
        service,
        detail,
        ts: new Date(),
      },
    });
  }, []);

  const notify = useCallback((side, message, variant = "info") => {
    const id = `notif-${Date.now()}-${Math.random()}`;
    dispatch({
      type: "PUSH_NOTIFICATION",
      payload: { id, side, message, variant, ts: new Date() },
    });
    setTimeout(
      () => dispatch({ type: "DISMISS_NOTIFICATION", payload: id }),
      12000,
    );
  }, []);

  const sendLocation = useCallback((driverId, lat, lng) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ driver_id: driverId, lat, lng }));
    }
  }, []);

  /* ── Driver movement animations ── */
  const moveToPickup = useCallback(
    async (driver, pickup, speed = 1) => {
      if (!driver || !pickup) return;
      dispatch({ type: "SET_DRIVER_MOVING", payload: true });
      await interpolate(driver, pickup, 14, speed, (lat, lng) => {
        dispatch({
          type: "UPDATE_DRIVER_POS",
          payload: { id: driver.id, lat, lng },
        });
        sendLocation(driver.id, lat, lng);
      });
      dispatch({ type: "SET_DRIVER_MOVING", payload: false });
      notify("driver", "Arrived at pickup location.", "success");
      notify("passenger", "Your driver has arrived!", "info");
    },
    [sendLocation, notify],
  );

  const moveToDestination = useCallback(
    async (driver, dropoff, speed = 1) => {
      if (!driver || !dropoff) return;
      dispatch({ type: "SET_DRIVER_MOVING", payload: true });
      await interpolate(driver, dropoff, 18, speed, (lat, lng) => {
        dispatch({
          type: "UPDATE_DRIVER_POS",
          payload: { id: driver.id, lat, lng },
        });
        sendLocation(driver.id, lat, lng);
      });
      dispatch({ type: "SET_DRIVER_MOVING", payload: false });
      notify("driver", "Arrived at destination.", "success");
    },
    [sendLocation, notify],
  );

  /* ── Animation trigger helper (called from both poll and WS paths) ── */
  const triggerAnim = useCallback(
    (tripId, newStatus) => {
      // Reset anim guard when a new trip starts
      if (tripId && tripId !== tripAnimRef.current.tripId) {
        tripAnimRef.current = {
          tripId,
          pickupStarted: false,
          dropoffStarted: false,
        };
      }

      if (
        newStatus === TRIP_STATUS.ACCEPTED &&
        !tripAnimRef.current.pickupStarted
      ) {
        tripAnimRef.current.pickupStarted = true;
        const { assignedDriver, pickup, speed } = stateRef.current;
        if (assignedDriver && pickup)
          moveToPickup(assignedDriver, pickup, speed);
      }

      if (
        newStatus === TRIP_STATUS.ON_TRIP &&
        !tripAnimRef.current.dropoffStarted
      ) {
        tripAnimRef.current.dropoffStarted = true;
        const { assignedDriver, dropoff, speed } = stateRef.current;
        if (assignedDriver && dropoff)
          moveToDestination(assignedDriver, dropoff, speed);
      }
    },
    [moveToPickup, moveToDestination],
  );

  /* ── Poll: reconcile UI with DB every 2s while trip is active ── */
  useEffect(() => {
    const { tripId, tripStatus } = state;
    if (!tripId) return;
    if (
      tripStatus === TRIP_STATUS.IDLE ||
      tripStatus === TRIP_STATUS.COMPLETED ||
      tripStatus === TRIP_STATUS.CANCELLED
    )
      return;

    const poll = setInterval(async () => {
      try {
        const res = await axios.get(`${API.ride}/v1/trips/${tripId}`);
        const trip = res.data;

        const hasDriver = !!trip.driver_id;
        const newStatus = dbStatusToUI(trip.status, hasDriver);
        const curStatus = stateRef.current.tripStatus;

        // Keep COMPLETED/CANCELLED once set (don't let poll overwrite)
        if (
          curStatus === TRIP_STATUS.COMPLETED ||
          curStatus === TRIP_STATUS.CANCELLED
        )
          return;

        // Sync assigned driver if it changed
        if (hasDriver) {
          const driver = DRIVER_SEEDS.find((d) => d.id === trip.driver_id);
          if (driver && stateRef.current.assignedDriver?.id !== driver.id) {
            dispatch({ type: "SET_DRIVER", payload: driver });
          }
        }

        // Sync status
        if (newStatus !== curStatus) {
          // Clear search timeout once a driver is found
          if (
            newStatus === TRIP_STATUS.ASSIGNED ||
            newStatus === TRIP_STATUS.ACCEPTED
          ) {
            if (searchTimeoutRef.current) {
              clearTimeout(searchTimeoutRef.current);
              searchTimeoutRef.current = null;
            }
          }
          dispatch({ type: "SET_STATUS", payload: newStatus });
          triggerAnim(tripId, newStatus);

          // Surface terminal transitions as notifications
          if (newStatus === TRIP_STATUS.CANCELLED) {
            notify("passenger", "Trip was cancelled.", "warning");
          }
          if (newStatus === TRIP_STATUS.COMPLETED) {
            notify(
              "passenger",
              "Trip completed! Thanks for riding with Vroom.",
              "success",
            );
            notify("driver", "Trip completed. Great job!", "success");
          }
        }
      } catch (err) {
        // Poll failures are silent — WS events still update the UI
      }
    }, 2000);

    return () => clearInterval(poll);
  }, [state.tripId, state.tripStatus, triggerAnim, notify]);

  /* ── WS: notification channel (real-time events + toast notifications) ── */
  const handleIncomingEvent = useCallback(
    (evt) => {
      const { event_type, payload } = evt;
      let data = {};
      try {
        data =
          typeof payload === "string" ? JSON.parse(payload) : (payload ?? {});
      } catch {
        data = payload ?? {};
      }

      console.log(`[WS] ${event_type}`, data);

      switch (event_type) {
        case "Trip.Requested":
          pushEvent("Trip.Requested", "ride", { tripId: data.id });
          break;

        case "Trip.Matched": {
          if (searchTimeoutRef.current) {
            clearTimeout(searchTimeoutRef.current);
            searchTimeoutRef.current = null;
          }
          const driver =
            DRIVER_SEEDS.find((d) => d.id === data.driver_id) ??
            DRIVER_SEEDS[0];
          dispatch({ type: "ASSIGN_DRIVER", payload: driver });
          pushEvent("Trip.Matched", "dispatch", { driverName: driver.name });
          notify("passenger", `Driver matched: ${driver.name}`, "success");
          notify("driver", "New ride offer! Accept or reject.", "info");
          break;
        }

        case "Trip.Accepted": {
          dispatch({ type: "SET_STATUS", payload: TRIP_STATUS.ACCEPTED });
          pushEvent("Trip.Accepted", "ride", {});
          notify("passenger", "Driver accepted! Heading to you.", "success");
          notify("driver", "Trip accepted. Navigating to passenger.", "info");
          triggerAnim(stateRef.current.tripId, TRIP_STATUS.ACCEPTED);
          break;
        }

        case "Trip.Started": {
          dispatch({ type: "SET_STATUS", payload: TRIP_STATUS.ON_TRIP });
          pushEvent("Trip.Started", "ride", {});
          notify("passenger", "Trip started! Enjoy your ride.", "success");
          triggerAnim(stateRef.current.tripId, TRIP_STATUS.ON_TRIP);
          break;
        }

        case "Trip.OfferRejected":
          dispatch({ type: "CLEAR_DRIVER" });
          pushEvent("Trip.OfferRejected", "dispatch", {
            driverId: data.driver_id,
          });
          notify(
            "passenger",
            "Driver unavailable. Looking for another...",
            "info",
          );
          notify("driver", "Offer rejected.", "warning");
          // Restart search timeout — dispatch is re-matching now
          if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
          searchTimeoutRef.current = setTimeout(() => {
            searchTimeoutRef.current = null;
            if (stateRef.current.tripStatus === TRIP_STATUS.SEARCHING) {
              dispatch({ type: "SET_STATUS", payload: TRIP_STATUS.IDLE });
              dispatch({ type: "SET_TRIP_ID", payload: null });
              notify(
                "passenger",
                "No driver found. Please try again.",
                "warning",
              );
            }
          }, SEARCH_TIMEOUT_MS);
          break;

        case "Trip.MatchFailed":
          if (searchTimeoutRef.current) {
            clearTimeout(searchTimeoutRef.current);
            searchTimeoutRef.current = null;
          }
          dispatch({ type: "SET_STATUS", payload: TRIP_STATUS.IDLE });
          dispatch({ type: "SET_TRIP_ID", payload: null });
          pushEvent("Trip.MatchFailed", "dispatch", { reason: data.reason });
          notify(
            "passenger",
            "No drivers available. Please try again.",
            "warning",
          );
          break;

        case "Trip.Cancelled":
          if (searchTimeoutRef.current) {
            clearTimeout(searchTimeoutRef.current);
            searchTimeoutRef.current = null;
          }
          dispatch({ type: "SET_STATUS", payload: TRIP_STATUS.CANCELLED });
          pushEvent("Trip.Cancelled", "ride", { reason: data.reason });
          notify("passenger", "Trip cancelled.", "warning");
          notify("driver", "Trip cancelled.", "warning");
          break;

        case "Trip.Completed":
          dispatch({ type: "SET_STATUS", payload: TRIP_STATUS.COMPLETED });
          pushEvent("Trip.Completed", "ride", {});
          notify(
            "passenger",
            "Trip completed! Thanks for riding with Vroom.",
            "success",
          );
          notify("driver", "Trip completed. Great job!", "success");
          break;

        default:
          pushEvent(event_type, "system", data);
      }
    },
    [pushEvent, notify, triggerAnim],
  );

  useEffect(() => {
    let dead = false;

    const connect = () => {
      if (dead) return;
      dispatch({ type: "SET_WS_STATUS", payload: "connecting" });
      const ws = new WebSocket(
        `${API.notificationWS}?userId=${DEMO_PASSENGER_ID}`,
      );

      ws.onopen = () => {
        dispatch({ type: "SET_WS_STATUS", payload: "connected" });
      };

      ws.onmessage = (evt) => {
        try {
          const data =
            typeof evt.data === "string" ? JSON.parse(evt.data) : evt.data;
          const uid =
            data.id ??
            data.correlation_id ??
            `${data.event_type}-${JSON.stringify(data.payload)}`;
          if (processedEventIds.current.has(uid)) return;
          processedEventIds.current.add(uid);
          if (processedEventIds.current.size > 200) {
            processedEventIds.current.delete(
              processedEventIds.current.values().next().value,
            );
          }
          handleIncomingEvent(data);
        } catch (err) {
          console.error("[WS] parse error", err);
        }
      };

      ws.onclose = () => {
        if (!dead) {
          dispatch({ type: "SET_WS_STATUS", payload: "disconnected" });
          setTimeout(connect, 3000);
        }
      };

      ws.onerror = () => {};
    };

    connect();
    return () => {
      dead = true;
    };
  }, [handleIncomingEvent]);

  /* ── Driver location heartbeat (keeps driver_last_seen TTL alive in dispatch) ── */
  useEffect(() => {
    if (state.drivers.length === 0) return;
    const iv = setInterval(() => {
      driversRef.current.forEach((d) => {
        axios
          .put(`${API.dispatch}/v1/drivers/${d.id}/location`, {
            lat: d.lat,
            lng: d.lng,
          })
          .catch(() => {});
        sendLocation(d.id, d.lat, d.lng);
      });
    }, 5000);
    return () => clearInterval(iv);
  }, [state.drivers.length, sendLocation]);

  /* ─────────────────────────────────────────────
     Actions
  ───────────────────────────────────────────── */
  const actions = {
    seedDrivers: async () => {
      dispatch({ type: "SEED_DRIVERS" });
      const results = await Promise.allSettled(
        DRIVER_SEEDS.map((d) =>
          axios.put(`${API.dispatch}/v1/drivers/${d.id}/location`, {
            lat: d.lat,
            lng: d.lng,
          }),
        ),
      );
      const ok = results.filter((r) => r.status === "fulfilled").length;
      const last = results.at(-1);
      logApi(
        "PUT",
        "/v1/drivers/:id/location",
        { count: DRIVER_SEEDS.length },
        last.status === "fulfilled" ? last.value.status : 0,
        `${ok}/${DRIVER_SEEDS.length} seeded`,
      );
      DRIVER_SEEDS.forEach((d) => sendLocation(d.id, d.lat, d.lng));
      pushEvent("Drivers.Seeded", "dispatch", { count: ok });
      notify("driver", `${ok} drivers online and ready.`, "success");
    },

    requestRide: async (p, d) => {
      const pickup = p ?? stateRef.current.pickup;
      const dropoff = d ?? stateRef.current.dropoff;

      if (stateRef.current.drivers.length === 0) {
        notify("passenger", "Seed drivers first.", "warning");
        return;
      }

      dispatch({ type: "SET_STATUS", payload: TRIP_STATUS.SEARCHING });
      dispatch({ type: "SET_ERROR", payload: null });
      notify("passenger", "Looking for a driver near you...", "info");

      const dist = Math.hypot(
        pickup.lat - dropoff.lat,
        pickup.lng - dropoff.lng,
      );
      const price = Math.max(30000, Math.round((dist * 800000) / 1000) * 1000);

      const payload = {
        source_lat: pickup.lat,
        source_lng: pickup.lng,
        dest_lat: dropoff.lat,
        dest_lng: dropoff.lng,
        estimated_price: price,
        currency: "VND",
      };

      try {
        const res = await axios.post(`${API.ride}/v1/trips`, payload, {
          headers: { "X-User-ID": DEMO_PASSENGER_ID },
        });
        const tripId = res.data?.trip_id ?? res.data?.id;
        dispatch({ type: "SET_TRIP_ID", payload: tripId });
        logApi("POST", "/v1/trips", payload, res.status, res.data);
        pushEvent("Trip.Requested", "ride", {
          tripId,
          pickup: pickup.label,
          dropoff: dropoff.label,
        });

        // Safety net: if no driver found within 20s via poll or WS, give up
        if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
        searchTimeoutRef.current = setTimeout(() => {
          searchTimeoutRef.current = null;
          if (stateRef.current.tripStatus === TRIP_STATUS.SEARCHING) {
            dispatch({ type: "SET_STATUS", payload: TRIP_STATUS.IDLE });
            dispatch({ type: "SET_TRIP_ID", payload: null });
            pushEvent("Trip.MatchTimeout", "system", { tripId });
            notify(
              "passenger",
              "No driver found after 20 s. Re-seed drivers and try again.",
              "warning",
            );
          }
        }, SEARCH_TIMEOUT_MS);
      } catch (err) {
        const msg =
          err.response?.data?.error ?? err.message ?? "Request failed";
        logApi(
          "POST",
          "/v1/trips",
          payload,
          err.response?.status ?? 0,
          err.message,
        );
        dispatch({ type: "SET_STATUS", payload: TRIP_STATUS.IDLE });
        dispatch({ type: "SET_ERROR", payload: msg });
        notify("passenger", `Could not create ride: ${msg}`, "warning");
      }
    },

    // Optimistic: immediately show ACCEPTED. Poll will confirm within 2s.
    acceptTrip: async (tripId, driverId) => {
      if (!tripId) return;
      const dId = driverId ?? stateRef.current.assignedDriver?.id;
      if (!dId) {
        notify("driver", "No driver assigned yet.", "warning");
        return;
      }
      const payload = { driver_id: dId };
      try {
        const res = await axios.post(
          `${API.ride}/v1/trips/${tripId}/accept`,
          payload,
        );
        logApi(
          "POST",
          `/v1/trips/${tripId}/accept`,
          payload,
          res.status,
          res.data,
        );
        // Optimistic state update — backend confirmed, DB is now ACCEPTED
        dispatch({ type: "SET_STATUS", payload: TRIP_STATUS.ACCEPTED });
        triggerAnim(tripId, TRIP_STATUS.ACCEPTED);
      } catch (err) {
        logApi(
          "POST",
          `/v1/trips/${tripId}/accept`,
          payload,
          err.response?.status ?? 0,
          err.message,
        );
        notify(
          "driver",
          `Accept failed: ${err.response?.data?.error ?? err.message}`,
          "warning",
        );
      }
    },

    rejectOffer: async (tripId, driverId) => {
      if (!tripId || !driverId) return;
      const payload = { driver_id: driverId };
      try {
        const res = await axios.post(
          `${API.ride}/v1/trips/${tripId}/reject`,
          payload,
        );
        logApi(
          "POST",
          `/v1/trips/${tripId}/reject`,
          payload,
          res.status,
          res.data,
        );
        // Optimistic: clear driver, go back to searching
        dispatch({ type: "CLEAR_DRIVER" });
        notify("passenger", "Driver declined. Looking for another...", "info");
        notify("driver", "Offer declined.", "warning");
        // Restart timeout for re-match window
        if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
        searchTimeoutRef.current = setTimeout(() => {
          searchTimeoutRef.current = null;
          if (stateRef.current.tripStatus === TRIP_STATUS.SEARCHING) {
            dispatch({ type: "SET_STATUS", payload: TRIP_STATUS.IDLE });
            dispatch({ type: "SET_TRIP_ID", payload: null });
            notify(
              "passenger",
              "No driver found. Please request again.",
              "warning",
            );
          }
        }, SEARCH_TIMEOUT_MS);
      } catch (err) {
        logApi(
          "POST",
          `/v1/trips/${tripId}/reject`,
          payload,
          err.response?.status ?? 0,
          err.message,
        );
        notify(
          "driver",
          `Reject failed: ${err.response?.data?.error ?? err.message}`,
          "warning",
        );
      }
    },

    // Optimistic: immediately show ON_TRIP.
    startTrip: async (tripId) => {
      if (!tripId) return;
      try {
        const res = await axios.post(`${API.ride}/v1/trips/${tripId}/start`);
        logApi("POST", `/v1/trips/${tripId}/start`, {}, res.status, res.data);
        dispatch({ type: "SET_STATUS", payload: TRIP_STATUS.ON_TRIP });
        triggerAnim(tripId, TRIP_STATUS.ON_TRIP);
        notify("passenger", "Trip started! Enjoy your ride.", "success");
      } catch (err) {
        logApi(
          "POST",
          `/v1/trips/${tripId}/start`,
          {},
          err.response?.status ?? 0,
          err.message,
        );
        notify(
          "driver",
          `Start failed: ${err.response?.data?.error ?? err.message}`,
          "warning",
        );
      }
    },

    // Optimistic: immediately show COMPLETED.
    completeTrip: async (tripId) => {
      if (!tripId) return;
      const finalPrice = stateRef.current.estimatedFare;
      const payload = { final_price: finalPrice };
      try {
        const res = await axios.post(
          `${API.ride}/v1/trips/${tripId}/complete`,
          payload,
        );
        logApi(
          "POST",
          `/v1/trips/${tripId}/complete`,
          payload,
          res.status,
          res.data,
        );
        dispatch({ type: "SET_STATUS", payload: TRIP_STATUS.COMPLETED });
        notify(
          "passenger",
          "Trip completed! Thanks for riding with Vroom.",
          "success",
        );
        notify("driver", "Trip completed. Great job!", "success");
        pushEvent("Trip.Completed", "ride", { tripId, finalPrice });
      } catch (err) {
        logApi(
          "POST",
          `/v1/trips/${tripId}/complete`,
          payload,
          err.response?.status ?? 0,
          err.message,
        );
        notify(
          "driver",
          `Complete failed: ${err.response?.data?.error ?? err.message}`,
          "warning",
        );
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
        const res = await axios.post(
          `${API.ride}/v1/trips/${tripId}/cancel`,
          payload,
        );
        logApi(
          "POST",
          `/v1/trips/${tripId}/cancel`,
          payload,
          res.status,
          res.data,
        );
      } catch (err) {
        logApi(
          "POST",
          `/v1/trips/${tripId}/cancel`,
          payload,
          err.response?.status ?? 0,
          err.message,
        );
      }
      // Optimistic cancel — always reflect it locally even if API failed
      dispatch({ type: "SET_STATUS", payload: TRIP_STATUS.CANCELLED });
      notify("passenger", "Trip cancelled.", "warning");
    },

    reset: async () => {
      if (searchTimeoutRef.current) {
        clearTimeout(searchTimeoutRef.current);
        searchTimeoutRef.current = null;
      }
      processedEventIds.current.clear();
      tripAnimRef.current = {
        tripId: null,
        pickupStarted: false,
        dropoffStarted: false,
      };

      // Cancel active trip if one exists and it's still cancellable
      const { tripId, tripStatus } = stateRef.current;
      if (
        tripId &&
        tripStatus !== TRIP_STATUS.COMPLETED &&
        tripStatus !== TRIP_STATUS.CANCELLED
      ) {
        try {
          await axios.post(`${API.ride}/v1/trips/${tripId}/cancel`, {
            reason: "Demo reset",
          });
        } catch {
          // Ignore — trip may already be in a terminal state
        }
      }

      dispatch({ type: "RESET" });
      pushEvent("System.Reset", "system", {});

      // Re-seed drivers after a short delay for the state to settle
      setTimeout(() => actions.seedDrivers(), 200);
    },

    setPickup: (loc) => dispatch({ type: "SET_PICKUP", payload: loc }),
    setDropoff: (loc) => dispatch({ type: "SET_DROPOFF", payload: loc }),
    setSpeed: (v) => dispatch({ type: "SET_SPEED", payload: v }),
    setAutoPlay: (v) => dispatch({ type: "SET_AUTO_PLAY", payload: v }),
    setStepMode: (v) => dispatch({ type: "SET_STEP_MODE", payload: v }),
    dismissNotif: (id) =>
      dispatch({ type: "DISMISS_NOTIFICATION", payload: id }),
    dismissError: () => dispatch({ type: "SET_ERROR", payload: null }),
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
   Linear interpolation between two lat/lng points
───────────────────────────────────────────── */
async function interpolate(from, to, steps, speed, onStep) {
  const delay = Math.max(80, 300 / speed);
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    onStep(
      from.lat + (to.lat - from.lat) * t,
      from.lng + (to.lng - from.lng) * t,
    );
    await new Promise((r) => setTimeout(r, delay));
  }
}
