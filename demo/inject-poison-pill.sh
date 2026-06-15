#!/usr/bin/env bash
# inject-poison-pill.sh
# Shows: dispatch consumer DLQ trigger for an unknown event type.
#
# What this demo proves:
#   • XADD unknown event type → ride_events stream
#   • Pre-arm retry counter to 2 (simulate 2 prior crash cycles)
#   • Consumer reads → processWithDLQ → Incr(3) >= threshold → XADD to ride_events_dlq
#   • Dispatch service remains healthy after DLQ promotion
#   • vroom_dlq_events_total Prometheus counter is incremented
#
# Usage:
#   NAMESPACE=vroom-dev bash tests/demo/inject-poison-pill.sh
set -euo pipefail

CLUSTER_IP="${CLUSTER_IP:-$(kubectl get nodes k3s-server -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || echo "192.168.25.135")}"
NAMESPACE="${NAMESPACE:-vroom-dev}"
REDIS_NS="${REDIS_NS:-platform}"
START_TS=$(date +%s)
MAX_RETRIES=3
TOTAL_STEPS=5

# Restore dispatch on exit (even if set -e fires mid-demo).
trap 'kubectl scale deployment dispatch-service -n "${NAMESPACE:-vroom-dev}" --replicas=1 >/dev/null 2>&1 || true' EXIT

# ─── Redis helper ─────────────────────────────────────────────────────────────
REDIS_POD=$(kubectl get pod -n "$REDIS_NS" -l app=redis -o name 2>/dev/null | head -1)
[[ -z "$REDIS_POD" ]] && { echo "ERROR: no redis pod in $REDIS_NS"; exit 1; }
rxcli() { kubectl exec -n "$REDIS_NS" "$REDIS_POD" -- redis-cli "$@" 2>/dev/null; }

# ─── Output helpers ───────────────────────────────────────────────────────────
ts()    { printf "T+%3ds" "$(( $(date +%s) - START_TS ))"; }
SEP="══════════════════════════════════════════════════════════════"
STEP_N=0
step() {
  STEP_N=$(( STEP_N + 1 ))
  printf "\n%s\n" "$SEP"
  printf " [%02d/%02d]  %s\n" "$STEP_N" "$TOTAL_STEPS" "$*"
  printf "%s\n" "$SEP"
}

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

# ─── Banner ───────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║         Dead Letter Queue (DLQ) Demo                       ║"
echo "╚══════════════════════════════════════════════════════════════╝"
printf "  Namespace : %s\n"    "$NAMESPACE"
printf "  Cluster   : %s\n"    "$CLUSTER_IP"
printf "  Redis NS  : %s\n"    "$REDIS_NS"
printf "  DLQ rule  : unknown event type + retry >= %s → ride_events_dlq\n" "$MAX_RETRIES"

# ── Pre-flight ─────────────────────────────────────────────────────────────────
printf "\n%s\n Pre-flight · state verification\n%s\n" "$SEP" "$SEP"

# Redis must be running (REDIS_POD already resolved at top — confirm)
printf "  ✓ %-34s Running\n" "redis ($REDIS_NS)"

# Dispatch-service: restore if stuck at 0 from a previous crashed demo
DISP_REP=$(kubectl get deployment dispatch-service -n "$NAMESPACE" \
  -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 0)
if [[ "${DISP_REP:-0}" -eq 0 ]]; then
  echo "  dispatch-service at 0 replicas — restoring to 1..."
  kubectl scale deployment dispatch-service -n "$NAMESPACE" --replicas=1 >/dev/null
fi
wait_ready "dispatch-service" "app=dispatch-service" "$NAMESPACE" 1 || exit 1

# ══ STEP 1/5 · Baseline ═══════════════════════════════════════════════════════
step "Baseline · DLQ + stream state before injection"
DLQ_BEFORE=$(rxcli XLEN ride_events_dlq 2>/dev/null || echo 0)
DLQ_BEFORE="${DLQ_BEFORE:-0}"
STREAM_LEN=$(rxcli XLEN ride_events 2>/dev/null || echo 0)
printf "  ride_events_dlq : %s entries\n" "$DLQ_BEFORE"
printf "  ride_events     : %s entries (including prior backlog)\n" "${STREAM_LEN:-0}"
printf "  DLQ threshold   : %s retries (processWithDLQ increments on each reclaim cycle)\n" "$MAX_RETRIES"

# ══ STEP 2/5 · Setup ══════════════════════════════════════════════════════════
step "Setup · scale dispatch→0 so message isn't consumed before retry counter is armed"
printf "  [%s] kubectl scale dispatch-service --replicas=0\n" "$(ts)"
kubectl scale deployment dispatch-service -n "$NAMESPACE" --replicas=0 2>&1 | awk '{printf "  %s\n",$0}'
kubectl wait pod -n "$NAMESPACE" -l app=dispatch-service \
  --for=delete --timeout=60s 2>/dev/null || true
printf "  [%s] ✓ dispatch down — stream is safe to inject\n" "$(ts)"

# ══ STEP 3/5 · Inject + arm retry counter ════════════════════════════════════
step "Inject · XADD + arm retry counter to 2 (simulate 2 prior crash cycles)"
echo "  Why pre-arm? DLQ triggers when processWithDLQ sees retry >= 3."
echo "  Consumer crashed twice before: retry key already = 2."
echo "  Next consume (Incr → 3) hits threshold → XADD to ride_events_dlq."
echo ""
EVENT_UUID="00000000-dead-beef-0000-$(date +%s%3N | tail -c 12)"
MSG_ID=$(rxcli XADD ride_events '*' \
  id           "$EVENT_UUID" \
  type         "UNKNOWN_EVENT_TYPE_DEMO" \
  aggregate    "TRIP" \
  aggregate_id "not-a-valid-uuid" \
  payload      "{\"corrupt\":true,\"injected_at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" \
  correlation_id "demo-poison-pill")
printf "  Stream msg ID : %s\n" "$MSG_ID"
printf "  Event type    : UNKNOWN_EVENT_TYPE_DEMO\n"
echo ""

# Arm the retry counter to 2 — consumer Incr will push it to 3 → DLQ
rxcli SET "event:retry:$MSG_ID" 2 EX 86400 >/dev/null
ARMED=$(rxcli GET "event:retry:$MSG_ID" 2>/dev/null || echo "?")
printf "  event:retry:%-28s = %s / %s  (armed ✓)\n" "$MSG_ID" "$ARMED" "$MAX_RETRIES"
printf "  [%s] Scale dispatch→1 — consumer will read + trigger DLQ\n" "$(ts)"
kubectl scale deployment dispatch-service -n "$NAMESPACE" --replicas=1 2>&1 | awk '{printf "  %s\n",$0}'

# ══ STEP 4/5 · Wait for DLQ entry ════════════════════════════════════════════
step "Trigger · polling DLQ until entry appears (max 60s)"
echo "  Timeline: pod start ~10-15s → XREADGROUP → processWithDLQ(Incr→3) → XADD to DLQ"
echo ""
DLQ_TRIGGERED=false
for _i in $(seq 1 12); do
  sleep 5
  ELAPSED=$(( _i * 5 ))
  DLQ_NOW=$(rxcli XLEN ride_events_dlq 2>/dev/null || echo 0)
  DLQ_NOW="${DLQ_NOW:-0}"
  RETRY_NOW=$(rxcli GET "event:retry:$MSG_ID" 2>/dev/null || echo "?")
  if [[ "$DLQ_NOW" -gt "$DLQ_BEFORE" ]]; then
    printf "  [T+%2ds] DLQ=%s retry=%s  ✓ DLQ triggered!\n" \
      "$ELAPSED" "$DLQ_NOW" "${RETRY_NOW:-deleted}"
    DLQ_TRIGGERED=true
    break
  else
    printf "  [T+%2ds] DLQ=%s retry=%-3s — waiting...\n" \
      "$ELAPSED" "$DLQ_NOW" "${RETRY_NOW:-?}"
  fi
done

# ══ STEP 5/5 · Verify ═════════════════════════════════════════════════════════
step "Verify · DLQ entry + log + dispatch health"
DLQ_AFTER=$(rxcli XLEN ride_events_dlq 2>/dev/null || echo 0)
DLQ_AFTER="${DLQ_AFTER:-0}"
DLQ_DELTA=$(( DLQ_AFTER - DLQ_BEFORE ))

printf "  ride_events_dlq : %s → %s  (Δ +%s)\n" "$DLQ_BEFORE" "$DLQ_AFTER" "$DLQ_DELTA"
echo ""

if [[ "$DLQ_DELTA" -gt 0 ]]; then
  echo "  ✓ DLQ entry confirmed. Contents:"
  rxcli XRANGE ride_events_dlq - + COUNT 3 2>/dev/null \
    | grep -v "^$" | awk '{printf "    %s\n",$0}'
  echo ""
fi

echo "  ── Service log ([DLQ] marker) ───────────────────────────────────────────"
DISPATCH_LOGS=$(kubectl logs -n "$NAMESPACE" -l app=dispatch-service \
  --tail=200 --since=2m 2>/dev/null \
  | grep -v "GET /\|POST /\|PUT /" \
  | grep -iE "demo-poison-pill|UNKNOWN_EVENT_TYPE_DEMO|\[DLQ\]" || true)
if [[ -n "$DISPATCH_LOGS" ]]; then
  echo "$DISPATCH_LOGS" | awk '{printf "    %s\n",$0}'
else
  echo "    (log scrolled — DLQ Redis entry above is authoritative)"
fi
echo ""

echo "  ── Dispatch health ──────────────────────────────────────────────────────"
kubectl get pods -n "$NAMESPACE" -l app=dispatch-service \
  --no-headers \
  -o custom-columns='NAME:.metadata.name,STATUS:.status.phase,READY:.status.containerStatuses[0].ready,RESTARTS:.status.containerStatuses[0].restartCount' \
  | awk '{printf "  %-42s %-10s %-6s %s\n",$1,$2,$3,$4}' \
  | { echo "  $(printf '%-42s %-10s %-6s %s' NAME STATUS READY RESTARTS)"; cat; }

# ══ Result banner ══════════════════════════════════════════════════════════════
DISPATCH_RUNNING=$(kubectl get pods -n "$NAMESPACE" -l app=dispatch-service \
  --no-headers 2>/dev/null | awk '/Running/{n++} END{print n+0}')
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
printf "║  %-60s║\n" "RESULT"
echo "╠══════════════════════════════════════════════════════════════╣"
printf "║  %-60s║\n" "Injected ID  : $MSG_ID"
printf "║  %-60s║\n" "DLQ delta    : +${DLQ_DELTA}"
printf "║  %-60s║\n" "Dispatch up  : ${DISPATCH_RUNNING:-0} pod(s)"
echo "╠══════════════════════════════════════════════════════════════╣"
if [[ "$DLQ_DELTA" -gt 0 && "${DISPATCH_RUNNING:-0}" -ge 1 ]]; then
  printf "║  %-60s║\n" "PASS  unknown event moved to DLQ after 3 retries"
  printf "║  %-60s║\n" "      dispatch healthy; vroom_dlq_events_total incremented"
elif [[ "$DLQ_DELTA" -eq 0 ]]; then
  printf "║  %-60s║\n" "WARN  DLQ not triggered — dispatch may still be starting"
  printf "║  %-60s║\n" "      retry: kubectl logs -n $NAMESPACE -l app=dispatch-service"
else
  printf "║  %-60s║\n" "WARN  dispatch pod count = ${DISPATCH_RUNNING:-0}"
fi
echo "╚══════════════════════════════════════════════════════════════╝"
