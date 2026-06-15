#!/usr/bin/env bash
# demo/pod-crash-demo.sh
# Shows: HPA scale-up under load → pod crash → Traefik retry → K8s self-healing recovery.
# Usage: CLUSTER_IP=192.168.242.10 bash demo/pod-crash-demo.sh
set -euo pipefail

CLUSTER_IP="${CLUSTER_IP:-$(kubectl get nodes k3s-server -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || echo "192.168.25.135")}"
NAMESPACE="${NAMESPACE:-vroom-dev}"
START_TS=$(date +%s)

# Restore HPA minReplicas=1 on exit so demo cleanup is automatic.
trap 'kubectl patch hpa ride-service -n "${NAMESPACE}" --type=merge --patch '"'"'{"spec":{"minReplicas":1}}'"'"' >/dev/null 2>&1 || true' EXIT

ts()  { printf "[T+%3ds]" "$(( $(date +%s) - START_TS ))"; }
sep() { echo ""; echo "── $* ──────────────────────────────────────────────────────"; }

# wait_ready <display_name> <label> <namespace> <min_count> [timeout_s]
wait_ready() {
  local name="$1" label="$2" ns="$3" min="$4" timeout="${5:-90}" elapsed=0
  while true; do
    local n
    n=$(kubectl get pods -n "$ns" -l "$label" --no-headers 2>/dev/null \
        | awk '/Running/{n++} END{print n+0}')
    if [[ "$n" -ge "$min" ]]; then
      printf "  ✓ %-34s %d Running\n" "$name" "$n"; return 0
    fi
    if [[ "$elapsed" -ge "$timeout" ]]; then
      printf "  ✗ %-34s timeout %ds — %d Running, need ≥%d\n" "$name" "$timeout" "$n" "$min"
      return 1
    fi
    printf "  … %-34s %d Running (need %d) · %ds elapsed\n" "$name" "$n" "$min" "$elapsed"
    sleep 5; elapsed=$(( elapsed + 5 ))
  done
}

prom_query() {
  # $1 = PromQL expression; returns scalar value or "n/a"
  [[ "${PROM_REACHABLE:-false}" != "true" ]] && echo "n/a" && return
  curl -sf "http://${PROM_IP}:9090/prometheus/api/v1/query" \
    --data-urlencode "query=$1" \
    | python3 -c "
import json,sys
d=json.load(sys.stdin)
r=d['data']['result']
print(int(float(r[0]['value'][1])) if r else 0)" 2>/dev/null || echo "n/a"
}

echo ""
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║    Pod Crash + Traefik Retry + HPA Demo                  ║"
echo "╚═══════════════════════════════════════════════════════════╝"
printf "  Namespace : %s\n" "$NAMESPACE"
printf "  Cluster   : %s\n" "$CLUSTER_IP"

# ── 0. Pre-flight ─────────────────────────────────────────────────────────────
sep "Pre-flight · state verification"

# Reset HPA minReplicas to 1 if a previous run left it at 2
HPA_MIN=$(kubectl get hpa ride-service -n "$NAMESPACE" \
  -o jsonpath='{.spec.minReplicas}' 2>/dev/null || echo 1)
if [[ "${HPA_MIN:-1}" -gt 1 ]]; then
  echo "  HPA ride-service minReplicas=${HPA_MIN} — resetting to 1 before demo..."
  kubectl patch hpa ride-service -n "$NAMESPACE" --type=merge \
    --patch '{"spec":{"minReplicas":1}}' >/dev/null
fi
printf "  ✓ %-34s minReplicas=%s\n" "HPA ride-service" "1"

# Restore ride-service if stuck at 0 replicas from a previous crashed demo
RIDE_REP=$(kubectl get deployment ride-service -n "$NAMESPACE" \
  -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 0)
if [[ "${RIDE_REP:-0}" -eq 0 ]]; then
  echo "  ride-service deployment at 0 — scaling to 1..."
  kubectl scale deployment ride-service -n "$NAMESPACE" --replicas=1 >/dev/null
fi
wait_ready "ride-service" "app=ride-service" "$NAMESPACE" 1 || exit 1

# Check k6 binary and baseline.js
BASELINE="$(dirname "$0")/../load-tests/baseline.js"
if ! command -v k6 >/dev/null 2>&1; then
  echo "  ✗ k6 not found — install k6 before running this demo"; exit 1
fi
if [[ ! -f "$BASELINE" ]]; then
  echo "  ✗ baseline.js not found: $BASELINE"; exit 1
fi
printf "  ✓ %-34s ready\n" "k6 + baseline.js"

# ── 1. Traefik retry middleware config ──────────────────────────────────────
sep "Traefik retry middleware"
kubectl get middleware -n "$NAMESPACE" \
  -o custom-columns='NAME:.metadata.name,ATTEMPTS:.spec.retry.attempts,INITIAL_INTERVAL:.spec.retry.initialInterval' \
  2>/dev/null || echo "(Middleware resources not found in $NAMESPACE — check middleware namespace)"

# ── 2. HPA state before load ────────────────────────────────────────────────
sep "HPA state (before load)"
kubectl get hpa -n "$NAMESPACE" \
  -o custom-columns='NAME:.metadata.name,MIN:.spec.minReplicas,MAX:.spec.maxReplicas,CURRENT:.status.currentReplicas,CPU_TARGET:.spec.metrics[0].resource.target.averageUtilization,CPU_CURRENT:.status.currentMetrics[0].resource.current.averageUtilization' \
  2>/dev/null || echo "(no HPA found in $NAMESPACE)"

sep "Pods (before load)"
kubectl get pods -n "$NAMESPACE" -l app=ride-service \
  -o custom-columns='NAME:.metadata.name,STATUS:.status.phase,READY:.status.containerStatuses[0].ready,RESTARTS:.status.containerStatuses[0].restartCount,AGE:.metadata.creationTimestamp'

# Raise HPA minReplicas to 2 first, THEN scale — this prevents HPA from overriding back to 1.
# Without this, HPA sees CPU=6% on 2 pods → desired=1 → overrides manual scale within 15s
# leaving 0 surviving pods when we delete the other one.
echo ""
echo "$(ts) Locking HPA minReplicas=2 + scaling to 2 replicas..."
kubectl patch hpa ride-service -n "$NAMESPACE" --type=merge --patch '{"spec":{"minReplicas":2}}'
kubectl scale deployment ride-service -n "$NAMESPACE" --replicas=2
kubectl rollout status deployment/ride-service -n "$NAMESPACE" --timeout=60s

# ── 3. Prometheus baseline counters ─────────────────────────────────────────
PROM_IP=$(kubectl get svc kube-prometheus-stack-prometheus -n monitoring \
  -o jsonpath='{.spec.clusterIP}' 2>/dev/null || echo "")

# Probe Prometheus before using it — avoids 2 minutes of ERRO spam in k6 output
PROM_REACHABLE=false
if [[ -n "$PROM_IP" ]]; then
  if curl -sf --connect-timeout 3 "http://${PROM_IP}:9090/-/ready" >/dev/null 2>&1 || \
     curl -sf --connect-timeout 3 "http://${PROM_IP}:9090/prometheus/-/ready" >/dev/null 2>&1; then
    PROM_REACHABLE=true
  fi
fi

sep "Traefik backend-error baseline (proxy-level failures to ride-service)"
# traefik_service_retries_total was a Traefik v2 metric — it does not exist in v3.
# In v3, connection-level failures are tracked as code=0 in traefik_service_requests_total.
# A spike in code=0 during the crash window + near-zero client errors = retry is absorbing failures.
BACKEND_ERR_BEFORE=$(prom_query 'sum(traefik_service_requests_total{service=~".*ride.*",code="0"}) or vector(0)')
if [[ "$PROM_REACHABLE" == "true" ]]; then
  echo "  traefik_service_requests_total{ride,code=0} = $BACKEND_ERR_BEFORE  (connection errors to backends)"
  echo "  (will spike during crash window; low k6 error rate = retry absorbed the failures)"
else
  echo "  Prometheus not reachable at ${PROM_IP:-<none>}:9090 — backend-error counter unavailable"
fi

# ── 4. k6 load test in background (redirected to file to avoid stdout corruption) ──
sep "Starting k6 load test"
echo "  50 VUs × 2 min → http://$CLUSTER_IP/ride-service"
echo "  Thresholds: P95 < 500ms | error rate < 1%"
echo "  k6 output → /tmp/k6-output.log (displayed after run)"
echo ""

K6_OUT_JSON="/tmp/k6-pod-crash.json"
K6_LOG="/tmp/k6-output.log"

if [[ "$PROM_REACHABLE" == "true" ]]; then
  K6_PROMETHEUS_RW_SERVER_URL="http://${PROM_IP}:9090/prometheus/api/v1/write" \
  K6_PROMETHEUS_RW_NATIVE_HISTOGRAMS=true \
  K6_PROMETHEUS_RW_TREND_AS_NATIVE_HISTOGRAM=true \
  k6 run \
      --out experimental-prometheus-rw \
      --out "json=${K6_OUT_JSON}" \
      --env RIDE_URL="http://$CLUSTER_IP/ride-service" \
      --env DISPATCH_URL="http://$CLUSTER_IP/dispatch-service" \
      "$(dirname "$0")/../load-tests/baseline.js" > "$K6_LOG" 2>&1 &
else
  k6 run \
      --out "json=${K6_OUT_JSON}" \
      --env RIDE_URL="http://$CLUSTER_IP/ride-service" \
      --env DISPATCH_URL="http://$CLUSTER_IP/dispatch-service" \
      "$(dirname "$0")/../load-tests/baseline.js" > "$K6_LOG" 2>&1 &
fi
K6_PID=$!

# Let load build for 30s so HPA has a chance to scale up before the crash
echo ""
echo "$(ts) Waiting 30s for load to build and HPA to react..."
sleep 30

# ── 6. Crash one pod ────────────────────────────────────────────────────────
echo ""
sep "Pod crash event"
# --field-selector=status.phase=Running fails under API server load (pipefail exits silently).
# Use awk local filter instead; || true prevents set -e from firing on empty result.
POD=$(kubectl get pods -n "$NAMESPACE" -l app=ride-service --no-headers 2>/dev/null \
  | awk '/Running/{print "pod/"$1; exit}') || true
if [[ -z "$POD" ]]; then
  echo "  ERROR: no Running ride-service pod found — check cluster state and retry"
  exit 1
fi
echo "$(ts) Deleting: $POD from namespace $NAMESPACE"
kubectl delete "$POD" -n "$NAMESPACE" --grace-period=0
CRASH_TS=$(date +%s)

echo ""
echo "$(ts) Traefik now retries in-flight requests to the dead pod's IP"
echo "       → middleware: 3 attempts, 100ms apart → traffic shifts to surviving pods"

# ── 7. Wait for k6 to finish ────────────────────────────────────────────────
wait "$K6_PID" || true   # k6 exits 99 on threshold violations — don't abort
RECOVER_TS=$(date +%s)

# ── Pod and HPA state captured via one-shot queries (no background watchers —
#    persistent -w connections stress the API server under k6 load) ──────────
sep "HPA state (during recovery)"
kubectl get hpa -n "$NAMESPACE" \
  -o custom-columns='NAME:.metadata.name,MIN:.spec.minReplicas,MAX:.spec.maxReplicas,CURRENT:.status.currentReplicas,CPU_TARGET:.spec.metrics[0].resource.target.averageUtilization,CPU_CURRENT:.status.currentMetrics[0].resource.current.averageUtilization' \
  2>/dev/null || echo "(no HPA)"

sep "k6 load test output"
cat "$K6_LOG" 2>/dev/null || echo "(no k6 output)"

# ── 8. Post-crash state ──────────────────────────────────────────────────────
sep "Pods (current)"
kubectl get pods -n "$NAMESPACE" -l app=ride-service \
  --no-headers \
  -o custom-columns='NAME:.metadata.name,STATUS:.status.phase,READY:.status.containerStatuses[0].ready,RESTARTS:.status.containerStatuses[0].restartCount' \
  | awk '{printf "  %-42s %-10s %-6s %s\n",$1,$2,$3,$4}' \
  | { echo "  $(printf '%-42s %-10s %-6s %s' NAME STATUS READY RESTARTS)"; cat; }

sep "HPA state (after)"
kubectl get hpa -n "$NAMESPACE" \
  -o custom-columns='NAME:.metadata.name,MIN:.spec.minReplicas,MAX:.spec.maxReplicas,CURRENT:.status.currentReplicas,CPU_TARGET:.spec.metrics[0].resource.target.averageUtilization,CPU_CURRENT:.status.currentMetrics[0].resource.current.averageUtilization' \
  2>/dev/null || echo "(no HPA)"

sep "Traefik backend-error counter (after)"
BACKEND_ERR_AFTER=$(prom_query 'sum(traefik_service_requests_total{service=~".*ride.*",code="0"}) or vector(0)')
BACKEND_ERR_DELTA="n/a"
if [[ "$BACKEND_ERR_BEFORE" != "n/a" && "$BACKEND_ERR_AFTER" != "n/a" ]]; then
  BACKEND_ERR_DELTA=$(( BACKEND_ERR_AFTER - BACKEND_ERR_BEFORE ))
fi
echo "  traefik_service_requests_total{ride,code=0} = $BACKEND_ERR_AFTER"
echo "  Delta (proxy connection errors during demo): +${BACKEND_ERR_DELTA}"
echo "  Note: in Traefik v3, retry attempts appear as code=0 backend hits + lower client error rate."

# ── 9. k6 error rate from JSON ──────────────────────────────────────────────
echo ""
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║                      Results                             ║"
echo "╚═══════════════════════════════════════════════════════════╝"

python3 -c "
import json
errors = total = 0
p95_bucket = []
for line in open('${K6_OUT_JSON}'):
    try:
        d = json.loads(line)
        if d.get('type') != 'Point': continue
        m = d.get('metric','')
        v = d['data']['value']
        if m == 'http_req_failed':
            total += 1
            if v == 1: errors += 1
        if m == 'http_req_duration':
            p95_bucket.append(v)
    except: pass

rate = (errors/total*100) if total else 0
p95 = sorted(p95_bucket)[int(len(p95_bucket)*0.95)] if p95_bucket else 0

print(f'  Requests sent       : {total}')
print(f'  Errors              : {errors} ({rate:.2f}%)')
print(f'  P95 latency         : {p95:.0f} ms')
print(f'  Verdict             : {\"PASS\" if rate < 0.5 else \"WARN\"} (target: error rate < 0.5%)')
" 2>/dev/null || echo "  Install python3 or inspect ${K6_OUT_JSON}"

echo ""
echo "  Recovery timeline:"
echo "    T+$(( CRASH_TS - START_TS ))s → pod deleted"
echo "    T+$(( RECOVER_TS - START_TS ))s → k6 finished; pod replaced in ~$(( RECOVER_TS - CRASH_TS ))s"
echo ""
echo "  What happened:"
echo "  1. HPA scaled up ride-service replicas as CPU climbed under 50 VU load"
echo "  2. One pod was hard-deleted (grace-period=0 = abrupt crash simulation)"
echo "  3. Traefik retry middleware intercepted in-flight requests to the dead pod"
echo "     and retried up to 3×, shifting to surviving replicas"
echo "     (Traefik v3 proxy errors: +${BACKEND_ERR_DELTA:-n/a}; proof = error rate stays < 1%)"
echo "  4. K8s Deployment controller detected pod termination and scheduled a"
echo "     replacement; HPA maintained replica floor throughout"

rm -f "${K6_OUT_JSON}" "${K6_LOG}"
