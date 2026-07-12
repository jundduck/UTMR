#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UTMR_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$UTMR_ROOT"

python3 "$SCRIPT_DIR/paper_experiments.py" "$@"
