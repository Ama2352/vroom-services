# Vroom Demo Scripts

Three runnable scenarios that prove resilience and scalability.

## Prerequisites

- `kubectl` configured against the cluster (`export KUBECONFIG=$(pwd)/vroom-infra/ansible/k3s.yaml`)
- `k6` installed locally
- A valid JWT: `TOKEN=$(curl -s -X POST http://$CLUSTER_IP/user-service/v1/auth/login -d '{"email":"...","password":"..."}' | jq -r .token)`

## 1. HPA Auto-scaling Demo

```bash
# Terminal 1 — watch replicas change
kubectl get hpa -n vroom-prod -w

# Terminal 2 — generate load
k6 run vroom-services/load-tests/spike.js
```

Expected: ride-service and dispatch-service scale from 2 → 4 replicas in ~60 seconds under 200 VU load. Grafana → Dashboards → Kubernetes HPA shows scaling events.

## 2. Pod Crash + Traefik Retry Demo

```bash
CLUSTER_IP=192.168.242.10 NAMESPACE=vroom-prod bash demo/pod-crash-demo.sh
```

Expected: k6 error rate < 0.5% despite one replica being killed mid-test. Traefik retries the failed request to the surviving replica.

## 3. Consumer Crash + XAUTOCLAIM Recovery Demo

```bash
CLUSTER_IP=192.168.242.10 NAMESPACE=vroom-prod TOKEN=<jwt> bash demo/consumer-crash-demo.sh
```

Expected: trip status is `ACCEPTED` after 35 seconds despite dispatch pod being killed mid-processing.

## 4. Dead Letter Queue Demo

```bash
NAMESPACE=vroom-prod bash demo/inject-poison-pill.sh
```

Expected: `ride_events_dlq` XLEN increases by 1 after 35 seconds. Grafana → Explore → Prometheus → `vroom_dlq_events_total` shows the counter increment.

## 5. Distributed Tracing Demo

1. Send one ride request through the frontend or `curl`
2. Open Grafana → Explore → select **Tempo** datasource
3. Search by Service Name: `ride-service`
4. Click a trace → spans for HTTP handler, DB query, outbox publish appear
5. Click the linked icon → navigate to `dispatch-service` and `notification-service` spans
