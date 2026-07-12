#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UTMR_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WOTE_ROOT="$UTMR_ROOT/third_party/WoTE"
OUT_DIR="$SCRIPT_DIR/results/navsim_received_subset"
MAX_SCENES=1
MODES=(baseline utmr)

usage() {
  cat <<USAGE
Usage: $0 [options]

Run WoTE/NAVSIM evaluation on navtest logs whose sensor blobs are already present.

Options:
  --out-dir PATH       Output directory, default experiments/utmr/results/navsim_received_subset.
  --max-scenes N       Number of matched scenes to evaluate, default 1.
  --modes MODE...      Modes to run, default: baseline utmr.
  -h, --help           Show this help.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out-dir)
      OUT_DIR="$2"
      shift 2
      ;;
    --max-scenes)
      MAX_SCENES="$2"
      shift 2
      ;;
    --modes)
      shift
      MODES=()
      while [[ $# -gt 0 && "$1" != --* ]]; do
        MODES+=("$1")
        shift
      done
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "${#MODES[@]}" -eq 0 ]]; then
  echo "--modes needs at least one mode" >&2
  exit 2
fi

mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"

source "$SCRIPT_DIR/source_wote_runtime.sh"
PYTHON_BIN="${PYTHON_BIN:-python3}"
mkdir -p "$OUT_DIR/raw" "$OUT_DIR/logs"

LOG_LIST="$(
  "$PYTHON_BIN" - "$UTMR_ROOT" "$MAX_SCENES" <<'PY'
from pathlib import Path
import sys
import yaml

root = Path(sys.argv[1])
max_scenes = int(sys.argv[2])
wote_root = root / "third_party" / "WoTE"
logs_dir = wote_root / "dataset" / "navsim_logs" / "test"
sensor_dir = wote_root / "dataset" / "sensor_blobs" / "test"
navtest_path = wote_root / "navsim" / "planning" / "script" / "config" / "common" / "scene_filter" / "navtest.yaml"

with navtest_path.open("r", encoding="utf-8") as stream:
    navtest = yaml.safe_load(stream)

required_dirs = {
    "CAM_F0",
    "CAM_L0",
    "CAM_L1",
    "CAM_L2",
    "CAM_R0",
    "CAM_R1",
    "CAM_R2",
    "CAM_B0",
    "MergedPointCloud",
}
metadata_logs = {path.stem for path in logs_dir.glob("*.pkl")}
complete_sensor_logs: set[str] = set()
for scene_dir in sensor_dir.iterdir() if sensor_dir.exists() else []:
    if not scene_dir.is_dir():
        continue
    child_dirs = {path.name for path in scene_dir.iterdir() if path.is_dir()}
    if required_dirs <= child_dirs:
        complete_sensor_logs.add(scene_dir.name)

navtest_logs = navtest.get("log_names") or []
matched = [name for name in navtest_logs if name in metadata_logs and name in complete_sensor_logs]
if len(matched) < max_scenes:
    raise SystemExit(f"only {len(matched)} complete navtest sensor logs available, need {max_scenes}")

print("[" + ",".join(matched[:max_scenes]) + "]")
PY
)"

METRIC_CACHE_PATH="$OUT_DIR/metric_cache" \
  "$WOTE_ROOT/scripts/evaluation/run_metric_caching.sh" \
  scene_filter=navtest \
  "scene_filter.log_names=$LOG_LIST" \
  "scene_filter.max_scenes=$MAX_SCENES" \
  worker=sequential \
  > "$OUT_DIR/logs/metric_cache.log" 2>&1

for mode in "${MODES[@]}"; do
  NUM_TRAJ_ANCHOR=64 \
  MODE="$mode" \
  UTMR_WOTE_STEP_LOG="$OUT_DIR/raw/${mode}_steps.jsonl" \
    "$SCRIPT_DIR/run_navsim_wote_eval.sh" \
    "experiment_name=eval/WoTE/default_${mode}_received_subset_${MAX_SCENES}" \
    "scene_filter.log_names=$LOG_LIST" \
    "scene_filter.max_scenes=$MAX_SCENES" \
    "metric_cache_path=$OUT_DIR/metric_cache" \
    worker=sequential \
    > "$OUT_DIR/logs/${mode}.log" 2>&1
done

printf 'saved   %s\n' "$OUT_DIR"
printf 'logs    %s\n' "$LOG_LIST"
