import os, re
import redis as redis_lib
from memory import store_incident, INDEX_KEY

DOCS_DIR = os.environ.get("DOCS_DIR", "/docs")


def _parse_vroom_ops(md_path: str) -> list:
    try:
        text = open(md_path).read()
    except FileNotFoundError:
        return []

    incidents = []
    sections = re.split(r'\n## ', text)
    for section in sections[1:]:
        lines = section.strip().split('\n')
        title = lines[0].strip()
        body = '\n'.join(lines[1:]).strip()

        symptom_m = re.search(r'Symptom:\s*(.+)', body)
        symptom = symptom_m.group(1).strip() if symptom_m else title

        # Extract root cause from body text (first substantive sentence)
        root_cause_m = re.search(r'\n([A-Z][^.\n]{20,80}\.)', body)
        root_cause = root_cause_m.group(1).strip() if root_cause_m else symptom

        restart_m = re.search(r'kubectl rollout restart deployment/(\S+)', body)
        remediation_tool = "restart_deployment" if restart_m else ""
        remediation_args = {"deployment": restart_m.group(1), "namespace": "<namespace>"} if restart_m else {}

        incidents.append({
            "alert_name":          title,
            "service":             "vroom",
            "namespace":           "vroom-dev",
            "symptoms":            symptom,
            "investigation_steps": [],
            "root_cause":          root_cause,
            "remediation_tool":    remediation_tool,
            "remediation_args":    remediation_args,
            "outcome":             "resolved",
        })
    return incidents


def seed_if_empty(rdb: redis_lib.Redis, docs_dir: str = DOCS_DIR) -> int:
    if rdb.scard(INDEX_KEY) > 0:
        return 0
    md_path = os.path.join(docs_dir, "vroom-ops.md")
    incidents = _parse_vroom_ops(md_path)
    for inc in incidents:
        store_incident(rdb, inc)
    return len(incidents)
