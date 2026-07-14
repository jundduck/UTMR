#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ ! -f "$ROOT/autoware/install/setup.bash" ]]; then
  echo "skip route guidance safety test: missing $ROOT/autoware/install/setup.bash"
  exit 0
fi
set +u
source "$ROOT/autoware/install/setup.bash"
set -u
PYTHONPATH="$ROOT:${PYTHONPATH:-}" python3 "$ROOT/experiments/utmr/test_route_guidance_safety.py"
