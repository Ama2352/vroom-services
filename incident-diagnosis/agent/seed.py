import os, re
import redis as redis_lib
from memory import store_knowledge_entry, store_history_entry, KNOWLEDGE_INDEX

DOCS_DIR = os.environ.get("DOCS_DIR", "/docs")

# Migrated verbatim from the old K8S_KNOWLEDGE_TABLE (interpreter.py) — this is the last
# place this content exists as Python source. After the one-time migration below it lives
# only in Redis (knowledge:entry:*), editable via the dashboard's Knowledge Base page.
# trigger_waiting_reason values and conclusive flags per the D3 table in the spec.
_BOOTSTRAP_KNOWLEDGE = {
    "init_oom": {
        "root_cause_pattern": "Init container OOMKilled — memory limit too low",
        "fix_action": "Increase the init container memory limit in the deployment manifest.",
        "trigger_waiting_reason": "Init:OOMKilled",
        "conclusive": True,
    },
    "init_crashloop": {
        "root_cause_pattern": "Init container crashing repeatedly (CrashLoopBackOff)",
        "fix_action": "Check kubectl logs <pod> -c <init-container-name> --previous for a "
                       "missing Secret/ConfigMap, unreachable dependency, or bad entrypoint.",
        "trigger_waiting_reason": "Init:CrashLoopBackOff",
        "conclusive": False,
    },
    "oom": {
        "root_cause_pattern": "Container exceeded its memory limit and was OOMKilled",
        "fix_action": "Increase the memory limit in the deployment manifest.",
        "trigger_waiting_reason": "OOMKilled",
        "conclusive": True,
    },
    "crashloop": {
        "root_cause_pattern": "Container exited with a non-zero code repeatedly (CrashLoopBackOff)",
        "fix_action": "Check previous container logs (kubectl logs <pod> --previous) for a "
                       "startup crash, missing env var, or unreachable dependency.",
        "trigger_waiting_reason": "CrashLoopBackOff",
        "conclusive": False,
    },
    "image_pull": {
        "root_cause_pattern": "Registry cannot pull the container image (ImagePullBackOff/ErrImagePull)",
        "fix_action": "Check the K8s event message for the exact image name; verify the tag "
                       "exists and registry credentials are correct.",
        "trigger_waiting_reason": "ImagePullBackOff",
        "conclusive": False,
    },
    "config_error": {
        "root_cause_pattern": "Pod spec references a Secret or ConfigMap that does not exist",
        "fix_action": "Create the missing Secret/ConfigMap named in the K8s event message.",
        "trigger_waiting_reason": "CreateContainerConfigError",
        "conclusive": True,
    },
    "failed_scheduling": {
        "root_cause_pattern": "No node can schedule the pod (FailedScheduling)",
        "fix_action": "Check node selector, resource requests, and taints/affinity rules "
                       "against available nodes.",
        "trigger_waiting_reason": "FailedScheduling",
        "conclusive": False,
    },
    "zero_replica": {
        "root_cause_pattern": "Deployment has zero running replicas with no waiting reason",
        "fix_action": "Check for an explicit scale-to-zero, HPA scale-down, or manual kubectl scale.",
        "trigger_waiting_reason": "ZeroReplicas",
        "conclusive": False,
    },
}


def _slugify(title: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', title.lower()).strip('_')


def _parse_vroom_ops(md_path: str) -> list:
    try:
        text = open(md_path).read()
    except FileNotFoundError:
        return []

    entries = []
    sections = re.split(r'\n## ', text)
    for section in sections[1:]:
        lines = section.strip().split('\n')
        title = lines[0].strip()
        body  = '\n'.join(lines[1:]).strip()

        symptom_m = re.search(r'Symptom:\s*(.+)', body)
        symptom   = symptom_m.group(1).strip() if symptom_m else title

        service_m = re.search(r'\bon ([\w.-]+)\s*$', title, re.IGNORECASE)
        service   = service_m.group(1) if service_m else "vroom"

        rc_m       = re.search(r'\n([A-Z][^.\n]{20,80}\.)', body)
        root_cause = rc_m.group(1).strip() if rc_m else symptom

        restart_m = re.search(r'kubectl rollout restart deployment/([\S]+)', body)
        scale_m   = re.search(r'kubectl scale deployment/([\S]+)', body)
        if restart_m:
            fix_action = f"kubectl rollout restart deployment/{restart_m.group(1)} -n <namespace>"
        elif scale_m:
            fix_action = f"kubectl scale deployment/{scale_m.group(1)} -n <namespace> --replicas=1"
        else:
            fix_action = ""

        entries.append({
            "title":      title,
            "service":    service,
            "symptom":    symptom,
            "root_cause": root_cause,
            "fix_action": fix_action,
        })
    return entries


def seed_if_empty(rdb: redis_lib.Redis, docs_dir: str = DOCS_DIR) -> int:
    """One-time D6 migration: populate `knowledge`/`history` from the static bootstrap
    table and vroom-ops.md, treating both as pre-approved (source="bootstrap"). No-op if
    `knowledge:index` is already non-empty."""
    if rdb.scard(KNOWLEDGE_INDEX) > 0:
        return 0

    count = 0
    for key, entry in _BOOTSTRAP_KNOWLEDGE.items():
        store_knowledge_entry(rdb, {
            "key":                    key,
            "root_cause_pattern":     entry["root_cause_pattern"],
            "fix_action":             entry["fix_action"],
            "trigger_waiting_reason": entry["trigger_waiting_reason"],
            "conclusive":             entry["conclusive"],
            "source":                 "bootstrap",
            "created_by":             "bootstrap",
        })
        count += 1

    md_path = os.path.join(docs_dir, "vroom-ops.md")
    for entry in _parse_vroom_ops(md_path):
        # D6 exception: "Pod OOMKilled" duplicates the existing `oom` key — collapse into
        # it as a history entry instead of creating a second knowledge entry.
        if "oomkilled" in entry["title"].lower():
            knowledge_key = "oom"
        else:
            knowledge_key = _slugify(entry["title"])
            store_knowledge_entry(rdb, {
                "key":                    knowledge_key,
                "root_cause_pattern":     entry["root_cause"],
                "fix_action":             entry["fix_action"],
                "trigger_waiting_reason": "",
                "conclusive":             False,
                "source":                 "bootstrap",
                "created_by":             "bootstrap",
            })
            count += 1
        store_history_entry(rdb, {
            "service":       entry["service"],
            "knowledge_key": knowledge_key,
            "symptom":       entry["symptom"],
            "context_notes": "",
            "source":        "bootstrap",
            "created_by":    "bootstrap",
        })
        count += 1

    return count
