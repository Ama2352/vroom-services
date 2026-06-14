#!/usr/bin/env bash
# demo/inject-poison-pill.sh
# Injects a malformed event into the ride_events stream.
# After 3 failed processing attempts, dispatch consumer moves it to ride_events_dlq.
set -euo pipefail

NAMESPACE="${NAMESPACE:-vroom-dev}"

echo "=== Dead Letter Queue Demo ==="

REDIS_POD=$(kubectl get pod -n "$NAMESPACE" -l app=redis -o name 2>/dev/null | head -1)
if [ -z "$REDIS_POD" ]; then
    echo "ERROR: No redis pod found in namespace $NAMESPACE"
    exit 1
fi

DLQ_BEFORE=$(kubectl exec -n "$NAMESPACE" "$REDIS_POD" -- redis-cli XLEN ride_events_dlq 2>/dev/null || echo 0)
echo "DLQ length before injection: $DLQ_BEFORE"

echo ""
echo "Injecting malformed event (unknown type, invalid aggregate_id)..."
MSG_ID=$(kubectl exec -n "$NAMESPACE" "$REDIS_POD" -- \
    redis-cli XADD ride_events '*' \
    type "UNKNOWN_EVENT_TYPE_DEMO" \
    aggregate_id "not-a-valid-uuid" \
    payload '{"corrupt":true,"injected_at":"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'"}')
echo "Injected message ID: $MSG_ID"

echo ""
echo "Waiting 35s for 3 retry cycles (dispatch consumer polls every ~5s)..."
sleep 35

DLQ_AFTER=$(kubectl exec -n "$NAMESPACE" "$REDIS_POD" -- redis-cli XLEN ride_events_dlq 2>/dev/null || echo 0)
echo "DLQ length after: $DLQ_AFTER"

if [ "$DLQ_AFTER" -gt "$DLQ_BEFORE" ]; then
    echo "PASS: Poison pill moved to DLQ"
    echo ""
    echo "DLQ contents:"
    kubectl exec -n "$NAMESPACE" "$REDIS_POD" -- redis-cli XRANGE ride_events_dlq - + | head -30
else
    echo "WARN: DLQ count did not increase — check dispatch-service logs"
    kubectl logs -n "$NAMESPACE" -l app=dispatch-service --tail=20 | grep -i "dlq\|retry\|unknown" || true
fi
