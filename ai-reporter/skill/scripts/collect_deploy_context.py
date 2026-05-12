#!/usr/bin/env python3
"""
Collects deployment context from the K8s API for verify mode.

Uses the in-cluster ServiceAccount token (mounted automatically in Kargo AnalysisRun Jobs).
Requires RBAC: ClusterRole ai-reporter-reader with get/list on deployments + pods.

Outputs deploy_context.json. Exits 0 on success, 1 on failure (non-fatal — reporter continues).

Env vars:
  TARGET_NAMESPACE  Namespace to inspect (default: vroom-dev)
"""
import json
import os
import sys
from datetime import datetime, timezone

import requests

NAMESPACE  = os.environ.get("TARGET_NAMESPACE", "vroom-dev")
K8S_API    = "https://kubernetes.default.svc"
TOKEN_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_FILE    = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"

SERVICES = ["user-service", "ride-service", "dispatch-service", "notification-service"]


def k8s_get(path: str) -> dict | None:
    try:
        with open(TOKEN_FILE) as f:
            token = f.read().strip()
        resp = requests.get(
            f"{K8S_API}{path}",
            headers={"Authorization": f"Bearer {token}"},
            verify=CA_FILE,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except FileNotFoundError:
        print(f"WARN: Not running in-cluster (token file not found) — skipping deploy context", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"WARN: K8s API call failed for {path}: {exc}", file=sys.stderr)
        return None


def main() -> int:
    context = {
        "namespace":       NAMESPACE,
        "deployed_images": {},
        "rollout_times":   {},
        "kargo_freight_id": "",
        "collected_at":    datetime.now(timezone.utc).isoformat(),
    }

    for svc in SERVICES:
        data = k8s_get(f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{svc}")
        if not data:
            continue

        # Extract current container image
        containers = (
            data.get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("containers", [])
        )
        for c in containers:
            if svc in c.get("name", ""):
                context["deployed_images"][svc] = c.get("image", "unknown")
                break

        # Extract rollout time from Progressing condition
        conditions = data.get("status", {}).get("conditions", [])
        for cond in conditions:
            if cond.get("type") == "Progressing" and cond.get("status") == "True":
                last_update = cond.get("lastUpdateTime", "")
                if last_update:
                    context["rollout_times"][svc] = last_update
                break

    # Scan pod annotations for Kargo freight ID
    pods_data = k8s_get(f"/api/v1/namespaces/{NAMESPACE}/pods")
    if pods_data:
        for pod in pods_data.get("items", []):
            annotations = pod.get("metadata", {}).get("annotations", {})
            freight_id = annotations.get("kargo.akuity.io/freight-id", "")
            if freight_id:
                context["kargo_freight_id"] = freight_id
                break

    try:
        with open("deploy_context.json", "w") as f:
            json.dump(context, f, indent=2)
        print(json.dumps(context, indent=2))
        print("STATUS: deploy context collected successfully")
        return 0
    except Exception as exc:
        print(f"ERROR: Failed to write deploy_context.json: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
