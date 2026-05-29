/**
 * Spike test — ramp from 0 → 200 VUs, then drain back to 0.
 *
 * Goal: validate the Outbox poller doesn't backlog under sudden surge.
 * A backlog manifests as rising P95 latency on the ride-creation endpoint
 * and increasing error rates as the Postgres connection pool saturates.
 *
 * Monitoring while running:
 *   kubectl port-forward -n monitoring svc/kube-prometheus-stack-prometheus 9090:9090
 *   Query: sum(ride_outbox_pending_events) or http_requests_total{service="ride"}
 *
 * Run:
 *   k6 run k6/spike.js
 *   k6 run k6/spike.js -e RIDE_URL=http://192.168.25.133/ride
 */

import http from 'k6/http';
import { sleep, check, group } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const errorRate   = new Rate('errors');
const outboxLag   = new Trend('outbox_apparent_lag_ms');

const RIDE_URL     = __ENV.RIDE_URL     || 'http://localhost:8082';
const DISPATCH_URL = __ENV.DISPATCH_URL || 'http://localhost:8083';

const PASSENGER_ID = '11111111-1111-1111-1111-111111111111';
const DRIVER_ID    = '22222222-2222-2222-2222-222222222222';

export const options = {
  scenarios: {
    spike: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '20s', target: 200 },  // spike ramp-up
        { duration: '1m',  target: 200 },  // hold at peak
        { duration: '20s', target: 0   },  // drain
      ],
    },
  },
  thresholds: {
    // Under spike, P95 must stay below 2 s
    'http_req_duration':                       ['p(95)<2000'],
    'http_req_duration{endpoint:request_ride}': ['p(95)<2000'],
    // Error budget: < 5% under spike load
    'errors': ['rate<0.05'],
  },
};

const rideHeaders = {
  'Content-Type': 'application/json',
  'X-User-ID':    PASSENGER_ID,
};

export function setup() {
  // Seed a driver so trips can be dispatched
  http.put(`${DISPATCH_URL}/v1/drivers/${DRIVER_ID}/location`, JSON.stringify({
    latitude:  10.762900,
    longitude: 106.660500,
  }), { headers: { 'Content-Type': 'application/json', 'X-User-ID': DRIVER_ID } });
}

export default function () {
  let tripID;
  const t0 = Date.now();

  group('request ride (spike)', () => {
    const res = http.post(
      `${RIDE_URL}/v1/trips`,
      JSON.stringify({
        source_lat:      10.762622,
        source_lng:      106.660172,
        dest_lat:        10.795202,
        dest_lng:        106.721519,
        estimated_price: 50000,
        currency:        'VND',
      }),
      { headers: rideHeaders, tags: { endpoint: 'request_ride' } },
    );

    const ok = check(res, {
      'trip created (201)': (r) => r.status === 201,
    });
    errorRate.add(!ok);

    if (ok) {
      tripID = res.json('trip_id');
      // Measure apparent Outbox lag: how long until the trip transitions from
      // REQUESTED → something else (dispatch picks it up via Redis Stream).
      // A growing lag here indicates Outbox poller backlog.
      pollForDispatch(tripID);
    }
  });

  sleep(0.2);
}

function pollForDispatch(tripID) {
  const deadline = Date.now() + 10000; // 10 s window (matches offer timeout)
  const start    = Date.now();

  while (Date.now() < deadline) {
    const r = http.get(
      `${RIDE_URL}/v1/trips/${tripID}`,
      { headers: { 'X-User-ID': PASSENGER_ID }, tags: { endpoint: 'poll_dispatch' } },
    );
    if (r.status === 200) {
      const status = r.json('status');
      if (status && status !== 'REQUESTED') {
        outboxLag.add(Date.now() - start);
        return;
      }
    }
    sleep(0.5);
  }
  // Timed out waiting for dispatch — record full window as lag
  outboxLag.add(Date.now() - start);
}
