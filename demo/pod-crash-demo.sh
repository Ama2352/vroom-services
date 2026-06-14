#!/usr/bin/env bash
# demo/pod-crash-demo.sh
# Shows Traefik retry + K8s LB routing traffic away from a crashed ride-service pod.
# Usage: CLUSTER_IP=192.168.242.10 bash demo/pod-crash-demo.sh
set -euo pipefail

CLUSTER_IP="${CLUSTER_IP:-192.168.242.10}"
NAMESPACE="${NAMESPACE:-vroom-dev}"

echo "=== Pod Crash + Traefik Retry Demo ==="
echo "Ensuring 2 ride-service replicas are running..."
kubectl scale deployment ride-service -n "$NAMESPACE" --replicas=2
kubectl rollout status deployment/ride-service -n "$NAMESPACE" --timeout=60s

echo ""
echo "Starting k6 load test in background (will run for 90s)..."
k6 run --out json=/tmp/k6-pod-crash.json \
    --env BASE_URL="http://$CLUSTER_IP" \
    vroom-services/load-tests/baseline.js &
K6_PID=$!

sleep 30
echo ""
echo "Killing one ride-service pod..."
POD=$(kubectl get pods -n "$NAMESPACE" -l app=ride-service -o name | head -1)
echo "  Deleting: $POD"
kubectl delete "$POD" -n "$NAMESPACE" --grace-period=0

echo "  Watching pod recovery..."
kubectl get pods -n "$NAMESPACE" -l app=ride-service -w &
WATCH_PID=$!

wait "$K6_PID"
kill "$WATCH_PID" 2>/dev/null || true

echo ""
echo "=== Results ==="
python3 -c "
import json, sys
errors = total = 0
for line in open('/tmp/k6-pod-crash.json'):
    try:
        d = json.loads(line)
        if d.get('type') == 'Point' and d.get('metric') == 'http_req_failed':
            total += 1
            if d['data']['value'] == 1:
                errors += 1
    except: pass
rate = (errors/total*100) if total else 0
print(f'Error rate: {rate:.2f}% ({errors}/{total} requests)')
print('PASS: error rate < 0.5%' if rate < 0.5 else 'WARN: error rate >= 0.5%')
" 2>/dev/null || echo "Install python3 or inspect /tmp/k6-pod-crash.json manually"
