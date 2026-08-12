#!/usr/bin/env bash
# Canonical copy of the DLQ contract-mismatch demo. Keep this script identical
# to ../../vroom-infra/inject-poison-pill.sh so validation and VM usage exercise
# the same real application path.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$SCRIPT_DIR/../../../vroom-infra/inject-poison-pill.sh" "$@"
