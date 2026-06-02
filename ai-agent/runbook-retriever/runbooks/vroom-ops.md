# Vroom Operations Runbook

## High error rate on ride-service

Symptom: http_requests_total with status 5xx rising above 1% in vroom-dev/staging/prod.

Check recent state transition errors: kubectl logs -n <namespace> -l app=ride-service --tail=100 | grep -i error

Look for ErrInvalidTripStatus — indicates out-of-order events from the dispatch consumer group. The saga state machine only allows REQUESTED→ACCEPTED→IN_PROGRESS→COMPLETED. An event arriving after a timeout cancellation causes this.

Mitigation: restart the dispatch-service pod to reset its XAUTOCLAIM cursor position.

## Outbox not draining

Symptom: outbox_events rows stay in PENDING state. No PUBLISHED rows appearing.

The OutboxWorker polls every 2 seconds and publishes to the ride_events Redis Stream. If the stream is full or Redis is unreachable, events back up.

Check worker logs: kubectl logs -n <namespace> -l app=ride-service | grep -i outbox

Check Redis memory pressure: kubectl exec -n <namespace> deploy/redis -- redis-cli info memory

If maxmemory is hit, the stream blocks new XADD calls. Set allkeys-lru eviction or increase memory limits.

## Driver matching returns 0 results

Symptom: GeoSearch on drivers:available returns empty. Passengers get no driver offers.

Dispatch stores driver location in Redis Geo key drivers:available. This key is in-memory only and lost on Redis restart.

Check current count: kubectl exec -n <namespace> deploy/redis -- redis-cli ZCARD drivers:available

Drivers must reconnect via WebSocket to re-register. Restarting dispatch-service does not restore driver state — drivers must reconnect themselves.

## Trip offer timeout storm

Symptom: many trips flipping to CANCELLED immediately after REQUESTED.

TripTimeoutWorker enforces a 10-second per-offer deadline. If dispatch is slow to respond or all drivers are busy, every offer times out and the trip is cancelled.

Check offer_deadline column: SELECT id, status, offer_deadline, updated_at FROM rides.trips WHERE status='CANCELLED' ORDER BY updated_at DESC LIMIT 20;

If offer_deadline values are all exactly 10s after created_at, this is expected behavior under load. If under normal load, check dispatch consumer lag.

## XAUTOCLAIM PEL backlog

Symptom: dispatch consumer processing stalls. Messages are claimed but not acknowledged.

Check pending entry list: kubectl exec -n <namespace> deploy/redis -- redis-cli XPENDING ride_events dispatch_group - + 100

Messages idle >30s are re-claimed by XAUTOCLAIM. If a dispatch pod crashed mid-processing, the same message is retried. This is safe — the saga state machine handles duplicate ACCEPTED events with ErrInvalidTripStatus which is logged and discarded.

## Pod OOMKilled

Symptom: pod restarts with OOMKilled in describe output.

10 GB RAM constraint across 3 VMs. Check node pressure: kubectl top nodes

Check pod memory: kubectl top pods -n <namespace> --sort-by=memory

Ride service limit is 300Mi. Dispatch is Redis-only so memory footprint is small. If ride or notification pods are OOMKilling, check for connection pool leaks or large in-memory caches.

## ArgoCD app stuck OutOfSync

Symptom: vroom-infrastructure or vroom-kargo-resources shows OutOfSync perpetually.

For SSA conflicts (multiple field managers): kubectl apply --server-side --force-conflicts -f <manifest>

For Kargo stage/warehouse rejections: verify the vroom namespace has the kargo.akuity.io/project=true label. kubectl label namespace vroom kargo.akuity.io/project=true --overwrite

## Kargo verification failing

Symptom: AnalysisRun for prometheus-checks shows Failed. Promotion blocked.

Check the AnalysisRun: kubectl describe analysisrun -n vroom

If error-rate metric fails: check Prometheus is scraping the target namespace. kubectl get servicemonitor -n <namespace>

If p99-latency metric fails under load: this may be a legitimate signal. Check if a bad deploy caused latency regression before forcing promotion.
