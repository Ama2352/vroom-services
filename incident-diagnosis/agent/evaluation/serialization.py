from evaluation.models import RankedCandidate, RetrievalCase


INCIDENT_FIELDS = (
    "waiting_reason",
    "last_terminated_reason",
    "init_waiting_reason",
    "init_last_terminated_reason",
    "event_reason",
    "event_message",
    "log_error",
)


def _clean(value) -> str:
    return " ".join(str(value).replace("\x00", " ").split())


def serialize_incident(case: RetrievalCase) -> str:
    lines = []
    if case.alert_name:
        lines.append(f"alert_name: {_clean(case.alert_name)}")
    for field in INCIDENT_FIELDS:
        value = case.facts.get(field)
        if value:
            lines.append(f"{field}: {_clean(value)}")
    dependency = case.facts.get("dependency")
    if isinstance(dependency, dict):
        for field in ("name", "waiting_reason", "pods_available", "pods_desired"):
            if dependency.get(field) is not None and dependency.get(field) != "":
                lines.append(f"dependency_{field}: {_clean(dependency[field])}")
    template_diff = case.facts.get("template_diff")
    if isinstance(template_diff, dict):
        for diff in template_diff.get("env_diff", []):
            if not isinstance(diff, dict):
                continue
            for field in ("key", "old_value", "new_value"):
                if diff.get(field) is not None and diff.get(field) != "":
                    lines.append(f"template_env_{field}: {_clean(diff[field])}")
        for field in ("old_image", "new_image"):
            if template_diff.get(field) is not None and template_diff.get(field) != "":
                lines.append(f"template_{field}: {_clean(template_diff[field])}")
    return "\n".join(lines)


def serialize_candidate(candidate: RankedCandidate) -> str:
    lines = [
        f"knowledge_key: {_clean(candidate.knowledge_key)}",
        f"document: {_clean(candidate.document_text)}",
        f"root_cause_pattern: {_clean(candidate.root_cause_pattern)}",
        f"fix_action: {_clean(candidate.fix_action)}",
    ]
    if candidate.context_notes:
        lines.append(f"approved_history_context: {_clean(candidate.context_notes)}")
    return "\n".join(lines)
