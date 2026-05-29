/**
 * Geo flood test — 200 simulated drivers each sending location every 2 seconds.
 *
 * Goal: assert that Redis GeoAdd + heartbeat SET P95 latency stays under 50 ms.
 * This validates the driver-location pipeline can handle 100 req/s sustained.
 *
 * Run:
 *   k6 run k6/geo_flood.js
 *   k6 run k6/geo_flood.js -e DISPATCH_URL=http://192.168.25.133/dispatch
 */

import http from 'k6/http';
import { sleep, check } from 'k6';
import { Rate } from 'k6/metrics';

const errorRate = new Rate('errors');

const DISPATCH_URL = __ENV.DISPATCH_URL || 'http://localhost:8083';

// Ho Chi Minh City bounding box for random driver positions
const LAT_MIN =  10.70;
const LAT_MAX =  10.85;
const LNG_MIN = 106.60;
const LNG_MAX = 106.75;

export const options = {
  // 200 concurrent "drivers", each sending one location update per 2 s iteration
  vus:      200,
  duration: '2m',
  thresholds: {
    // SLO: Redis GeoAdd P95 must complete in < 50 ms
    'http_req_duration{endpoint:location_update}': ['p(95)<50'],
    // Overall P95 under 100 ms (includes dispatch service overhead)
    'http_req_duration':                           ['p(95)<100'],
    // Error budget: < 1%
    'errors': ['rate<0.01'],
  },
};

export default function () {
  // Each VU represents one driver; derive a stable UUID from the VU number.
  const driverID = `driver-${__VU.toString().padStart(6, '0')}-flood-test-uuid`;

  // Random position within HCMC bounding box
  const lat = LAT_MIN + Math.random() * (LAT_MAX - LAT_MIN);
  const lng = LNG_MIN + Math.random() * (LNG_MAX - LNG_MIN);

  const res = http.put(
    `${DISPATCH_URL}/v1/drivers/${driverID}/location`,
    JSON.stringify({ latitude: lat, longitude: lng }),
    {
      headers: {
        'Content-Type': 'application/json',
        'X-User-ID':    driverID,
      },
      tags: { endpoint: 'location_update' },
    },
  );

  const ok = check(res, {
    'location accepted (200)': (r) => r.status === 200 || r.status === 204,
  });
  errorRate.add(!ok);

  // Sleep to maintain ~2 s cadence per VU (matches production driver heartbeat)
  sleep(2);
}
