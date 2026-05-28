/**
 * Baseline load test — 50 VUs sustained for 2 minutes.
 *
 * Goal: establish P95 latency baseline for the core ride-request flow under
 * steady-state load. Run before any optimization changes so you have a reference.
 *
 * Run:
 *   k6 run k6/baseline.js
 *   k6 run k6/baseline.js -e RIDE_URL=http://192.168.25.133/ride
 */

import http from 'k6/http';
import { sleep, check, group } from 'k6';
import { Rate } from 'k6/metrics';

const errorRate = new Rate('errors');

const RIDE_URL    = __ENV.RIDE_URL    || 'http://localhost:8082';
const DISPATCH_URL = __ENV.DISPATCH_URL || 'http://localhost:8083';

// A fixed passenger UUID for the load test — no real auth needed in dev.
const PASSENGER_ID = '11111111-1111-1111-1111-111111111111';
const DRIVER_ID    = '22222222-2222-2222-2222-222222222222';

export const options = {
  vus: 50,
  duration: '2m',
  thresholds: {
    // Core SLO: 95th percentile response time under 500 ms
    'http_req_duration':                   ['p(95)<500'],
    // Ride-creation endpoint must be fast: P95 < 1 s
    'http_req_duration{endpoint:request_ride}': ['p(95)<1000'],
    // Error rate below 1%
    'errors':                              ['rate<0.01'],
  },
};

const rideHeaders = {
  'Content-Type': 'application/json',
  'X-User-ID':    PASSENGER_ID,
};

export function setup() {
  // Prime the driver into the geo index so RequestRide can be dispatched
  http.put(`${DISPATCH_URL}/v1/drivers/${DRIVER_ID}/location`, JSON.stringify({
    latitude:  10.762900,
    longitude: 106.660500,
  }), { headers: { 'Content-Type': 'application/json', 'X-User-ID': DRIVER_ID } });
}

export default function () {
  let tripID;

  group('request ride', () => {
    const res = http.post(
      `${RIDE_URL}/v1/trips`,
      JSON.stringify({
        source_lat:       10.762622,
        source_lng:       106.660172,
        dest_lat:         10.795202,
        dest_lng:         106.721519,
        estimated_price:  50000,
        currency:         'VND',
      }),
      { headers: rideHeaders, tags: { endpoint: 'request_ride' } },
    );

    const ok = check(res, {
      'trip created (201)': (r) => r.status === 201,
    });
    errorRate.add(!ok);

    if (ok) {
      tripID = res.json('trip_id');
    }
  });

  sleep(0.5);

  if (tripID) {
    group('get trip', () => {
      const res = http.get(
        `${RIDE_URL}/v1/trips/${tripID}`,
        { headers: rideHeaders, tags: { endpoint: 'get_trip' } },
      );
      check(res, { 'trip found (200)': (r) => r.status === 200 });
    });
  }

  sleep(1);
}
