import os, re
import redis as redis_lib
from memory import store_runbook_entry, RUNBOOK_INDEX

DOCS_DIR = os.environ.get("DOCS_DIR", "/docs")


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

        # Extract service name from section title
        # "High error rate on ride-service" → "ride-service"
        service_m = re.search(r'\bon ([\w.-]+)\s*$', title, re.IGNORECASE)
        service   = service_m.group(1) if service_m else "vroom"

        # Extract root cause — first substantive sentence in body
        rc_m       = re.search(r'\n([A-Z][^.\n]{20,80}\.)', body)
        root_cause = rc_m.group(1).strip() if rc_m else symptom

        # Prefer rollout restart, fall back to scale, else empty
        restart_m = re.search(r'kubectl rollout restart deployment/([\S]+)', body)
        scale_m   = re.search(r'kubectl scale deployment/([\S]+)', body)
        if restart_m:
            fix_command = f"kubectl rollout restart deployment/{restart_m.group(1)} -n <namespace>"
        elif scale_m:
            fix_command = f"kubectl scale deployment/{scale_m.group(1)} -n <namespace> --replicas=1"
        else:
            fix_command = ""

        entries.append({
            "title":       title,
            "service":     service,
            "symptom":     symptom,
            "root_cause":  root_cause,
            "fix_command": fix_command,
            "source":      "bootstrap",
        })
    return entries


def seed_if_empty(rdb: redis_lib.Redis, docs_dir: str = DOCS_DIR) -> int:
    if rdb.scard(RUNBOOK_INDEX) > 0:
        return 0
    md_path = os.path.join(docs_dir, "vroom-ops.md")
    entries = _parse_vroom_ops(md_path)
    for entry in entries:
        store_runbook_entry(rdb, entry)
    return len(entries)
