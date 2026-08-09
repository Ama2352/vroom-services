"""Fetch one incident read-only and write a sanitized causal-proof artifact."""

import argparse
import json
from pathlib import Path
import sys

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_proof import build_sanitized_proof


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("incident_id")
    parser.add_argument("--base-url", default="http://localhost:5002")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    response = requests.get(f"{args.base_url.rstrip('/')}/incidents/{args.incident_id}", timeout=15)
    response.raise_for_status()
    incident = response.json().get("incident")
    if not isinstance(incident, dict):
        raise SystemExit("incident response did not contain an incident object")
    Path(args.output).write_text(json.dumps(build_sanitized_proof(incident), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
