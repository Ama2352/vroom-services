#!/usr/bin/env bash
# consumer-crash-demo.sh
# Shows XAUTOCLAIM recovery with a clear PEL timeline:
#   T=0  — synthetic event injected, claimed by "crash-simulator" → PEL=1
#   T+5..30 — new pod starting; idle counter increments; PEL stays 1
#   T+30+ — XAUTOCLAIM fires → PEL=0
#
# Usage (dev, no JWT):
#   bash tests/demo/consumer-crash-demo.sh
# Usage (with JWT):
#   TOKEN=<jwt> bash tests/demo/consumer-crash-demo.sh
set -euo pipefail

CLUSTER_IP="${CLUSTER_IP:-$(kubectl get nodes k3s-server -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || echo "192.168.25.135")}"
NAMESPACE="${NAMESPACE:-vroom-dev}"
REDIS_NS="${REDIS_NS:-platform}"
TOKEN="${TOKEN:-}"
START_TS=$(date +%s)

PASSENGER_ID="11111111-1111-1111-1111-111111111111"
DRIVER_ID="22222222-2222-2222-2222-222222222222"
TOTAL_STEPS=8

# Always restore dispatch to 1 replica on exit (even if set -e fires mid-demo).
trap 'kubectl scale deployment dispatch-service -n "${NAMESPACE:-vroom-dev}" --replicas=1 >/dev/null 2>&1 || true' EXIT

# ─── Auth helpers ─────────────────────────────────────────────────────────────
if [[ -n "$TOKEN" ]]; then
  pcurl() { curl -s -H "Authorization: Bearer $TOKEN" -H "X-User-ID: $PASSENGER_ID" -H "Content-Type: application/json" "$@"; }
  dcurl() { curl -s -H "Authorization: Bearer $TOKEN" -H "X-User-ID: $DRIVER_ID"    -H "Content-Type: application/json" "$@"; }
  AUTH_MODE="JWT Bearer token"
else
  pcurl() { curl -s -H "X-User-ID: $PASSENGER_ID" -H "Content-Type: application/json" "$@"; }
  dcurl() { curl -s -H "X-User-ID: $DRIVER_ID"    -H "Content-Type: application/json" "$@"; }
  AUTH_MODE="dev passthrough (X-User-ID headers)"
fi

# ─── Redis helper ─────────────────────────────────────────────────────────────
# Redis runs in the platform namespace, not per-app namespace.
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
http() {
  local label="$1" method="$2" url="$3" body="${4:-}"
  printf "  %-12s %s %s\n" "$label" "$method" "$url"
  if [[ -n "$body" ]]; then
    printf "  %-12s %s\n" "body:" "$body"
  fi
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
echo "║      Consumer Crash + XAUTOCLAIM Recovery Demo             ║"
echo "╚══════════════════════════════════════════════════════════════╝"
printf "  Auth      : %s\n"    "$AUTH_MODE"
printf "  Namespace : %s\n"    "$NAMESPACE"
printf "  Cluster   : %s\n"    "$CLUSTER_IP"
printf "  Redis NS  : %s\n"    "$REDIS_NS"

# ── Pre-flight ─────────────────────────────────────────────────────────────────
printf "\n%s\n Pre-flight · state verification\n%s\n" "$SEP" "$SEP"

# Redis must be running (already checked above — just confirm)
printf "  ✓ %-34s Running\n" "redis ($REDIS_NS)"

# Dispatch-service: restore if stuck at 0 from a previous crashed demo
DISP_REP=$(kubectl get deployment dispatch-service -n "$NAMESPACE" \
  -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 0)
if [[ "${DISP_REP:-0}" -eq 0 ]]; then
  echo "  dispatch-service at 0 replicas — restoring to 1..."
  kubectl scale deployment dispatch-service -n "$NAMESPACE" --replicas=1 >/dev/null
fi
wait_ready "dispatch-service" "app=dispatch-service" "$NAMESPACE" 1 || exit 1

# Ride-service must be Running to create the trip in Step 3
wait_ready "ride-service" "app=ride-service" "$NAMESPACE" 1 || exit 1

# ══ STEP 1/8 · Consumer group state (before) ══════════════════════════════════
step "Pre-flight · consumer group state"
XINFO_RAW=$(rxcli XINFO GROUPS ride_events 2>/dev/null || true)
if [[ -z "$XINFO_RAW" ]] || [[ "$XINFO_RAW" == \(* ]]; then
  echo "  (no consumer groups yet)"
else
  echo "$XINFO_RAW" | awk '
    BEGIN {
      printf "  %-24s %9s  %7s  %5s\n","GROUP","CONSUMERS","PENDING","LAG"
      printf "  %-24s %9s  %7s  %5s\n","------------------------","---------","-------","-----"
    }
    /^name$/      { getline name }
    /^consumers$/ { getline con }
    /^pending$/   { getline pend }
    /^lag$/       { getline lag; printf "  %-24s %9s  %7s  %5s\n",name,con,pend,lag }
  '
fi

# ══ STEP 2/8 · Dispatch pod state (before) ════════════════════════════════════
step "Pre-flight · dispatch pod state"
kubectl get pods -n "$NAMESPACE" -l app=dispatch-service \
  --no-headers \
  -o custom-columns='NAME:.metadata.name,STATUS:.status.phase,READY:.status.containerStatuses[0].ready,RESTARTS:.status.containerStatuses[0].restartCount' \
  | awk '{printf "  %-42s %-10s %-6s %s\n",$1,$2,$3,$4}' \
  | { echo "  $(printf '%-42s %-10s %-6s %s' NAME STATUS READY RESTARTS)"; cat; }

# ══ STEP 3/8 · Register driver + create trip ══════════════════════════════════
step "Setup · register driver location + create trip"
printf "  Clearing prior driver saga state for %s...\n" "$DRIVER_ID"
rxcli DEL "driver_status:$DRIVER_ID"    > /dev/null 2>&1 || true
rxcli DEL "driver_last_seen:$DRIVER_ID" > /dev/null 2>&1 || true
echo ""

REG_URL="http://$CLUSTER_IP/dispatch-service/v1/drivers/$DRIVER_ID/location"
REG_BODY='{"lat":10.762622,"lng":106.660172}'
http "[$(ts)]" "PUT" "$REG_URL" "$REG_BODY"
REG_RESP=$(dcurl -X PUT "$REG_URL" -d "$REG_BODY")
printf "  %-12s %s\n" "response:" "$REG_RESP"
echo ""

TRIP_URL="http://$CLUSTER_IP/ride-service/v1/trips"
TRIP_BODY='{"source_lat":10.762622,"source_lng":106.660172,"dest_lat":10.795202,"dest_lng":106.721519,"estimated_price":50000,"currency":"VND"}'
http "[$(ts)]" "POST" "$TRIP_URL"
printf "  %-12s %s\n" "body:" "$TRIP_BODY"
TRIP_RESP=$(pcurl -X POST "$TRIP_URL" -d "$TRIP_BODY")
printf "  %-12s %s\n" "response:" "$TRIP_RESP"
TRIP_ID=$(echo "$TRIP_RESP" | jq -r '.trip_id // .id // empty')
[[ -z "$TRIP_ID" ]] && { echo "  ERROR: could not parse trip_id"; exit 1; }
printf "  %-12s %s\n" "trip_id:" "$TRIP_ID"

# ══ STEP 4/8 · Wait for dispatch to assign driver ═════════════════════════════
step "Wait · dispatch assigns driver via Trip.Matched (max 20s)"
echo "  OutboxWorker publishes every 2s → dispatch matches → ride service sets driver_id"
echo ""
DRIVER_ASSIGNED=false
for i in $(seq 1 20); do
  sleep 1
  POLL_RESP=$(pcurl "http://$CLUSTER_IP/ride-service/v1/trips/$TRIP_ID")
  ASSIGNED=$(echo "$POLL_RESP" | jq -r '.driver_id // empty' 2>/dev/null || true)
  if [[ -n "$ASSIGNED" && "$ASSIGNED" != "null" ]]; then
    DRIVER_ASSIGNED=true
    printf "  [%s] ✓ driver_id = %s\n" "$(ts)" "$ASSIGNED"
    break
  fi
  printf "  [%s]   attempt %2d — driver_id not set yet\n" "$(ts)" "$i"
done
[[ "$DRIVER_ASSIGNED" != "true" ]] && { echo "  ✗ ERROR: driver not assigned within 20s"; exit 1; }

# ══ STEP 5/8 · Driver accepts ════════════════════════════════════════════════
step "Action · driver accepts trip (offer_deadline = 10s)"
ACCEPT_URL="http://$CLUSTER_IP/ride-service/v1/trips/$TRIP_ID/accept"
ACCEPT_BODY="{\"driver_id\":\"$DRIVER_ID\"}"
http "[$(ts)]" "POST" "$ACCEPT_URL" "$ACCEPT_BODY"
ACCEPT_RESP=$(dcurl -X POST "$ACCEPT_URL" -d "$ACCEPT_BODY")
printf "  %-12s %s\n" "response:" "$ACCEPT_RESP"
ACCEPT_STATUS=$(echo "$ACCEPT_RESP" | jq -r '.status // "?"' 2>/dev/null || true)
if [[ "$ACCEPT_STATUS" == "ACCEPTED" ]]; then
  printf "  %-12s %s\n" "result:" "✓ trip is now ACCEPTED"
else
  printf "  %-12s %s\n" "result:" "⚠ unexpected status: $ACCEPT_STATUS"
fi

# ══ STEP 6/8 · PEL setup: scale to 0 → inject → XREADGROUP ═══════════════════
step "PEL Setup · scale dispatch to 0, inject event, claim into PEL as crash-simulator"
echo "  Why scale to 0 first? If dispatch is running it processes messages in < 100ms,"
echo "  closing the PEL window before we can observe it. Scaling to 0 eliminates the race."
echo ""

echo "  ① Scale dispatch deployment replicas → 0 (no consumer can steal the message)."
printf "  [%s] kubectl scale deployment dispatch-service -n %s --replicas=0\n" "$(ts)" "$NAMESPACE"
kubectl scale deployment dispatch-service -n "$NAMESPACE" --replicas=0 2>&1 | awk '{printf "  %s\n",$0}'
printf "  [%s] waiting for pod to terminate...\n" "$(ts)"
kubectl wait pod -n "$NAMESPACE" -l app=dispatch-service \
  --for=delete --timeout=60s 2>/dev/null || true
printf "  [%s] ✓ all dispatch pods terminated — PEL is now safe to write\n" "$(ts)"
echo ""

echo "  ② Inject a synthetic Trip.Accepted event into the stream."
FAKE_EVENT_ID=$(cat /proc/sys/kernel/random/uuid 2>/dev/null \
  || python3 -c 'import uuid; print(uuid.uuid4())')
FAKE_PAYLOAD="{\"id\":\"$TRIP_ID\",\"driver_id\":\"$DRIVER_ID\",\"status\":\"ACCEPTED\"}"
STREAM_MSG_ID=$(rxcli XADD ride_events '*' \
  id           "$FAKE_EVENT_ID" \
  type         "Trip.Accepted"  \
  aggregate    "TRIP"           \
  aggregate_id "$TRIP_ID"       \
  payload      "$FAKE_PAYLOAD"  \
  correlation_id "crash-demo-$$")
printf "  Stream msg ID : %s\n" "$STREAM_MSG_ID"
printf "  Event UUID    : %s\n" "$FAKE_EVENT_ID"
echo ""

echo "  ③ XREADGROUP as 'crash-simulator' — simulates a consumer that read the"
echo "     message but crashed before XACK.  The message now sits in PEL indefinitely."
# Single-quoted '>' passes the Redis > token (not a shell redirect).
rxcli XREADGROUP GROUP dispatch_group crash-simulator COUNT 1 STREAMS ride_events '>' > /dev/null 2>&1 || true
INJECT_TS=$(date +%s)
echo ""

echo "  ── XPENDING at T=0 (right after claiming) ───────────────────────────────"
echo "  XPENDING summary (count / first-id / last-id / consumers):"
rxcli XPENDING ride_events dispatch_group | awk '{printf "    %s\n",$0}'
echo ""
echo "  XPENDING detail  (msg-id  |  consumer  |  idle-ms  |  deliveries):"
rxcli XPENDING ride_events dispatch_group - + 5 | awk '{printf "    %s\n",$0}'
echo ""
PCOUNT_T0=$(rxcli XPENDING ride_events dispatch_group | head -1 | tr -d '[:space:]')
printf "  dispatch_group pending: %s  ← PEL is populated ✓\n" "${PCOUNT_T0:-0}"

echo ""
echo "  ④ Scale back to 1 — K8s schedules a replacement pod."
echo "     New pod's XAUTOCLAIM loop runs every ~5s with MinIdle=30s."
printf "  [%s] kubectl scale deployment dispatch-service -n %s --replicas=1\n" "$(ts)" "$NAMESPACE"
kubectl scale deployment dispatch-service -n "$NAMESPACE" --replicas=1 2>&1 | awk '{printf "  %s\n",$0}'

# ══ STEP 7/8 · PEL timeline + pod recovery ════════════════════════════════════
step "Chaos · PEL countdown — polling every 10s until XAUTOCLAIM fires"
echo "  XAUTOCLAIM MinIdle=30s: the crash-simulator entry must be idle ≥ 30s before"
echo "  the replacement pod can claim it.  Watch PEL stay at 1, then drop to 0."
echo ""

printf "  %-9s  %-5s  %-6s  %s\n" "WALL" "IDLE" "PEL" "NOTE"
printf "  %-9s  %-5s  %-6s  %s\n" "---------" "-----" "------" "------------------------------------------------------"
PEL_CLEARED=false
for _i in $(seq 1 7); do
  sleep 10
  ELAPSED=$(( $(date +%s) - INJECT_TS ))
  PCOUNT=$(rxcli XPENDING ride_events dispatch_group | head -1 | tr -d '[:space:]')
  if [[ "${PCOUNT:-0}" == "0" ]]; then
    printf "  [T+%2ds]   %3ds   %-6s  ✓ XAUTOCLAIM fired — PEL cleared!\n" \
      "$ELAPSED" "$ELAPSED" "0"
    PEL_CLEARED=true
    break
  else
    if   [[ "$ELAPSED" -lt 15 ]]; then NOTE="pod still initializing"
    elif [[ "$ELAPSED" -lt 30 ]]; then NOTE="pod running, idle ${ELAPSED}s < 30s — XAUTOCLAIM waiting"
    else                               NOTE="idle ${ELAPSED}s ≥ 30s — XAUTOCLAIM imminent"
    fi
    printf "  [T+%2ds]   %3ds   %-6s  %s\n" "$ELAPSED" "$ELAPSED" "${PCOUNT:-?}" "$NOTE"
  fi
done

echo ""
echo "  ── Pod recovery (current state) ──────────────────────────────────────"
kubectl get pods -n "$NAMESPACE" -l app=dispatch-service \
  --no-headers \
  -o custom-columns='NAME:.metadata.name,STATUS:.status.phase,READY:.status.containerStatuses[0].ready,RESTARTS:.status.containerStatuses[0].restartCount' \
  | awk '{printf "    %-42s %-10s %-6s %s\n",$1,$2,$3,$4}'

if [[ "$PEL_CLEARED" != "true" ]]; then
  PCOUNT_NOW=$(rxcli XPENDING ride_events dispatch_group | head -1 | tr -d '[:space:]')
  printf "\n  ⚠  PEL not cleared yet (%s pending) — XAUTOCLAIM may need more time.\n" \
    "${PCOUNT_NOW:-?}"
fi

# ══ STEP 8/8 · Verify: trip + PEL + logs ═════════════════════════════════════
step "Verify · trip status + final PEL + XAUTOCLAIM evidence in logs"

echo "  ── Trip status ──────────────────────────────────────────────"
VERIFY_URL="http://$CLUSTER_IP/ride-service/v1/trips/$TRIP_ID"
http "" "GET" "$VERIFY_URL"
TRIP_FINAL=$(pcurl "$VERIFY_URL")
printf "  %-12s %s\n" "response:" \
  "$(echo "$TRIP_FINAL" | jq -c '{trip_id:(.trip_id//.id),status:.status,driver_id:.driver_id}')"
STATUS_VAL=$(echo "$TRIP_FINAL" | jq -r '.status // "unknown"')
if [[ "$STATUS_VAL" == "ACCEPTED" ]]; then
  printf "  %-12s ✓ %s\n" "result:" "$STATUS_VAL"
else
  printf "  %-12s ✗ expected ACCEPTED, got %s\n" "result:" "$STATUS_VAL"
fi

echo ""
echo "  ── Final XPENDING ───────────────────────────────────────────"
PCOUNT_FINAL=$(rxcli XPENDING ride_events dispatch_group | head -1 | tr -d '[:space:]')
printf "  dispatch_group pending: %s\n" "${PCOUNT_FINAL:-0}"
if [[ "${PCOUNT_FINAL:-0}" == "0" ]]; then
  echo "  ✓ All messages ACK'd — replacement pod claimed crash-simulator's stranded entry"
else
  echo "  ⚠ ${PCOUNT_FINAL} still pending — XAUTOCLAIM may still be running"
fi

echo ""
echo "  ── Dispatch logs (XAUTOCLAIM / SAGA / IDEMPOTENCY markers) ──"
DISPATCH_LOGS=$(kubectl logs -n "$NAMESPACE" -l app=dispatch-service --tail=60 2>/dev/null \
  | grep -iE "autoclaim|MATCH|SAGA|Trip\.|reclaim|IDEMPOTENCY" || true)
if [[ -n "$DISPATCH_LOGS" ]]; then
  echo "$DISPATCH_LOGS" | awk '{printf "    %s\n",$0}'
else
  echo "    (no matching lines — pod may still be starting up)"
fi

# ══ Result banner ══════════════════════════════════════════════════════════════
ELAPSED_TOTAL=$(( $(date +%s) - INJECT_TS ))
PCOUNT_VAL="${PCOUNT_FINAL:-0}"
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
printf "║  %-60s║\n" "RESULT"
echo "╠══════════════════════════════════════════════════════════════╣"
printf "║  Trip ID     : %-47s║\n" "$TRIP_ID"
if [[ "$STATUS_VAL" == "ACCEPTED" ]]; then
  printf "║  Trip Status : %-47s║\n" "ACCEPTED  ✓"
else
  printf "║  Trip Status : %-47s║\n" "$STATUS_VAL  ✗"
fi
printf "║  PEL cleared : %-47s║\n" "~${ELAPSED_TOTAL}s after injection (MinIdle=30s)"
printf "║  PEL final   : %-47s║\n" "${PCOUNT_VAL} pending"
echo "╠══════════════════════════════════════════════════════════════╣"
if [[ "$STATUS_VAL" == "ACCEPTED" && "${PCOUNT_VAL:-0}" == "0" ]]; then
  printf "║  %-60s║\n" "PASS  trip ACCEPTED + XAUTOCLAIM cleared PEL"
  printf "║  %-60s║\n" "      crash-simulator entry claimed after MinIdle ≥ 30s"
else
  printf "║  %-60s║\n" "PARTIAL — check dispatch logs above for errors"
fi
echo "╚══════════════════════════════════════════════════════════════╝"
