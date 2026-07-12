#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UTMR_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WOTE_ROOT="${WOTE_ROOT:-$UTMR_ROOT/third_party/WoTE}"
PY_SITE="$UTMR_ROOT/runtime/python-packages"

export WOTE_ROOT
export PYTHONPATH="$PY_SITE:$WOTE_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export NUPLAN_MAP_VERSION="${NUPLAN_MAP_VERSION:-nuplan-maps-v1.0}"
export NUPLAN_MAPS_ROOT="${NUPLAN_MAPS_ROOT:-$WOTE_ROOT/dataset/maps}"
export NAVSIM_EXP_ROOT="${NAVSIM_EXP_ROOT:-$WOTE_ROOT/exp}"
export NAVSIM_DEVKIT_ROOT="${NAVSIM_DEVKIT_ROOT:-$WOTE_ROOT}"
export OPENSCENE_DATA_ROOT="${OPENSCENE_DATA_ROOT:-$WOTE_ROOT/dataset}"
