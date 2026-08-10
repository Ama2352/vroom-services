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


def _legacy_facts(facts) -> dict:
    if not hasattr(facts, "to_prompt_dict"):
        return facts
    projected = facts.to_prompt_dict()
    legacy = {}
    for key, value in projected.items():
        if key.startswith("runtime."):
            legacy[key.split(".", 1)[1]] = value
        elif key.startswith("dependency."):
            legacy.setdefault("dependency", {})[key.split(".", 1)[1]] = value
    return legacy


def extract_canonical_signals(facts) -> frozenset[str]:
    facts = _legacy_facts(facts)
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


def select_unique_signal(facts) -> str:
    signals = extract_canonical_signals(facts)
    return next(iter(signals)) if len(signals) == 1 else ""

