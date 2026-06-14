#!/usr/bin/env bash
# demo/consumer-crash-demo.sh
# Shows XAUTOCLAIM recovery: dispatch consumer crashes mid-processing,
# event stays in PEL, new consumer reclaims and processes it after 30s.
# Usage: CLUSTER_IP=192.168.242.10 TOKEN=<jwt> bash demo/consumer-crash-demo.sh
set -euo pipefail

CLUSTER_IP="${CLUSTER_IP:-192.168.242.10}"
NAMESPACE="${NAMESPACE:-vroom-dev}"
TOKEN="${TOKEN:-}"

if [ -z "$TOKEN" ]; then
    echo "ERROR: Set TOKEN to a valid driver or passenger JWT"
    exit 1
fi

echo "=== Consumer Crash + XAUTOCLAIM Recovery Demo ==="

DRIVER_ID="00000000-0000-0000-0000-000000000001"
echo "Setting driver $DRIVER_ID as available at (10.762622, 106.660172)..."
curl -s -X PUT "http://$CLUSTER_IP/dispatch-service/v1/drivers/$DRIVER_ID/location" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"driver_id":"'"$DRIVER_ID"'","lat":10.762622,"lng":106.660172}' | jq .

echo ""
echo "Creating ride request..."
TRIP=$(curl -s -X POST "http://$CLUSTER_IP/ride-service/v1/trips" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"source_lat":10.762622,"source_lng":106.660172,"dest_lat":10.77,"dest_lng":106.67}' | jq -r .id)
echo "Trip created: $TRIP"

sleep 1

echo ""
echo "Killing dispatch-service pod (simulating crash mid-processing)..."
kubectl delete pod -n "$NAMESPACE" -l app=dispatch-service --grace-period=0

echo "Waiting 35s for XAUTOCLAIM threshold (30s MinIdle)..."
sleep 35

echo ""
echo "Checking trip status (expect ACCEPTED — recovered by XAUTOCLAIM):"
curl -s "http://$CLUSTER_IP/ride-service/v1/trips/$TRIP" \
    -H "Authorization: Bearer $TOKEN" | jq '{id:.id, status:.status}'

echo ""
echo "Checking dispatch-service logs for autoclaim:"
kubectl logs -n "$NAMESPACE" -l app=dispatch-service --tail=20 | grep -i "autoclaim\|MATCH\|SAGA" || echo "(no autoclaim log yet — pod may still be starting)"
