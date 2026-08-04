#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-vroom-dev}"
AGENT_URL="${AGENT_URL:-http://192.168.242.10:30081}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
REPORT_DIR="${REPORT_DIR:-validation/reports}"
mkdir -p "$REPORT_DIR"

for command in kubectl curl jq; do
  command -v "$command" >/dev/null || { echo "missing required command: $command" >&2; exit 2; }
done

for component in ride-service dispatch-service notification-service; do
  kubectl -n "$NAMESPACE" get deployment "$component" >/dev/null
done

response="$(curl --fail-with-body --silent --show-error "$AGENT_URL/incidents/latest")"
printf '%s\n' "$response" > "$REPORT_DIR/correlated-incident-$RUN_ID.json"
jq -e '.incident != null' <<<"$response" >/dev/null
cat > "$REPORT_DIR/correlated-incident-$RUN_ID.md" <<EOF
# Correlated incident validation

- Run: `$RUN_ID`
- Namespace: `$NAMESPACE`
- Agent response captured from `$AGENT_URL/incidents/latest`

## Automated checks

- [x] required commands and application deployments available
- [x] latest incident endpoint returned an incident
- [ ] dashboard confidence card visually verified
- [ ] Grafana trace waterfall visually verified
EOF
echo "wrote $REPORT_DIR/correlated-incident-$RUN_ID.json and .md"
