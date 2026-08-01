from __future__ import annotations

import re
from typing import Any


INCIDENT_FIELDS = (
    "waiting_reason",
    "last_terminated_reason",
    "init_waiting_reason",
    "init_last_terminated_reason",
    "event_reason",
    "event_message",
    "log_error",
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _clean(value: Any) -> str:
    return " ".join(str(value).replace("\x00", " ").split())


def extract_canonical_signals(facts: dict) -> frozenset[str]:
    signals: set[str] = set()

    init_last = facts.get("init_last_terminated_reason")
    if init_last and init_last != "Unknown":
        signals.add(f"Init:{init_last}")
    init_waiting = facts.get("init_waiting_reason")
    if init_waiting:
        signals.add(f"Init:{init_waiting}")

    last = facts.get("last_terminated_reason")
    if last and last != "Unknown":
        signals.add(str(last))
    waiting = facts.get("waiting_reason")
    if waiting:
        signals.add("ImagePullBackOff" if waiting == "ErrImagePull" else str(waiting))

    if facts.get("pods_available", 0) == 0 and facts.get("pods_desired", 0) > 0:
        signals.add("ZeroReplicas")

    dependency = facts.get("dependency")
    if isinstance(dependency, dict) and dependency.get("name"):
        name = dependency["name"]
        if dependency.get("pods_desired") == 0:
            signals.add(f"Dependency:{name}:ZeroReplicas")
        if dependency.get("waiting_reason"):
            signals.add(f"Dependency:{name}:{dependency['waiting_reason']}")
        if (
            dependency.get("pods_desired") is not None
            and dependency.get("pods_available") != dependency.get("pods_desired")
        ):
            signals.add(f"Dependency:{name}:Unhealthy")

    if facts.get("event_reason"):
        signals.add(str(facts["event_reason"]))
    return frozenset(signals)


def select_unique_signal(facts: dict) -> str:
    signals = extract_canonical_signals(facts)
    return next(iter(signals)) if len(signals) == 1 else ""


def serialize_incident(alert_name: str, facts: dict) -> str:
    lines: list[str] = []
    if alert_name:
        lines.append(f"alert_name: {_clean(alert_name)}")
    for field in INCIDENT_FIELDS:
        value = facts.get(field)
        if value:
            lines.append(f"{field}: {_clean(value)}")

    dependency = facts.get("dependency")
    if isinstance(dependency, dict):
        for field in ("name", "waiting_reason", "pods_available", "pods_desired"):
            if dependency.get(field) is not None and dependency.get(field) != "":
                lines.append(f"dependency_{field}: {_clean(dependency[field])}")

    template_diff = facts.get("template_diff")
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


def serialize_reranker_query(alert_name: str, facts: dict) -> str:
    """Return a natural-language value projection for the cross-encoder.

    BM25 benefits from field labels; the MiniLM model was trained on ordinary
    text pairs, so labels such as ``waiting_reason:`` can distract it. Keep the
    same collected fields but present only their values to the semantic scorer.
    """
    # Alert names are retained in the BM25 projection, but many are synthetic
    # rule identifiers (for example ``KubePodContainerWaiting``) and hurt the
    # cross-encoder's natural-language relevance score.
    values: list[str] = []
    for field in INCIDENT_FIELDS:
        value = facts.get(field)
        if value:
            values.append(_clean(value))
    dependency = facts.get("dependency")
    if isinstance(dependency, dict):
        for field in ("name", "waiting_reason", "pods_available", "pods_desired"):
            if dependency.get(field) is not None and dependency.get(field) != "":
                values.append(_clean(dependency[field]))
    return " ".join(values)
