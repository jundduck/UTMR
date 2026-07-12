#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UTMR_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WOTE_ROOT="$UTMR_ROOT/third_party/WoTE"
NUM_TRAJ_ANCHOR="${NUM_TRAJ_ANCHOR:-64}"
MIN_METRIC_CACHE_ROWS="${MIN_METRIC_CACHE_ROWS:-1000}"

missing=0

require_file() {
  local path="$1"
  if [[ -f "$path" ]]; then
    printf 'ok      %s\n' "$path"
  else
    printf 'missing %s\n' "$path"
    missing=1
  fi
}

require_nonempty_dir() {
  local path="$1"
  if [[ -d "$path" ]] && find "$path" -mindepth 1 -print -quit | grep -q .; then
    printf 'ok      %s\n' "$path"
  else
    printf 'missing %s\n' "$path"
    missing=1
  fi
}

require_test_logs() {
  local path="$1"
  local count
  count="$(find "$path" -maxdepth 1 -type f -name '*.pkl' 2>/dev/null | wc -l)"
  if [[ "$count" -gt 0 ]]; then
    printf 'ok      %s (%s pkl files)\n' "$path" "$count"
  else
    printf 'missing %s (expected direct *.pkl files)\n' "$path"
    missing=1
  fi
}

require_test_sensors() {
  local path="$1"
  local expected_count="$2"
  local count
  if [[ -d "$path" ]]; then
    count="$(find "$path" -mindepth 1 -maxdepth 1 -type d | wc -l)"
  else
    count=0
  fi
  if [[ "$count" -ge "$expected_count" ]] && [[ "$count" -gt 0 ]]; then
    printf 'ok      %s (%s scene dirs)\n' "$path" "$count"
  elif [[ "$count" -gt 0 ]]; then
    printf 'partial %s (%s scene dirs, expected at least %s)\n' "$path" "$count" "$expected_count"
    missing=1
  else
    printf 'missing %s\n' "$path"
    missing=1
  fi
}

require_metric_cache() {
  local path="$1"
  local rows=0
  local csv
  if [[ -d "$path/metadata" ]]; then
    while IFS= read -r csv; do
      rows=$((rows + $(awk 'END { print (NR > 0 ? NR - 1 : 0) }' "$csv")))
    done < <(find "$path/metadata" -maxdepth 1 -type f -name '*.csv')
  fi
  if [[ "$rows" -ge "$MIN_METRIC_CACHE_ROWS" ]]; then
    printf 'ok      %s (%s metadata rows)\n' "$path" "$rows"
  elif [[ "$rows" -gt 0 ]]; then
    printf 'partial %s (%s metadata rows, expected at least %s)\n' "$path" "$rows" "$MIN_METRIC_CACHE_ROWS"
    missing=1
  else
    printf 'missing %s\n' "$path"
    missing=1
  fi
}

printf 'UTMR root: %s\n' "$UTMR_ROOT"
printf 'WoTE root: %s\n' "$WOTE_ROOT"
printf 'trajectory anchors: %s\n\n' "$NUM_TRAJ_ANCHOR"

require_file "$WOTE_ROOT/ckpts/resnet34.pth"
require_file "$WOTE_ROOT/exp/WoTE/default/lightning_logs/version_0/checkpoints/epoch=29-step=19950.ckpt"
require_nonempty_dir "$WOTE_ROOT/dataset/maps"
require_test_logs "$WOTE_ROOT/dataset/navsim_logs/test"
test_log_count="$(find "$WOTE_ROOT/dataset/navsim_logs/test" -maxdepth 1 -type f -name '*.pkl' 2>/dev/null | wc -l)"
require_test_sensors "$WOTE_ROOT/dataset/sensor_blobs/test" "$test_log_count"
require_file "$WOTE_ROOT/dataset/extra_data/planning_vb/trajectory_anchors_${NUM_TRAJ_ANCHOR}.npy"
require_file "$WOTE_ROOT/dataset/extra_data/planning_vb/formatted_pdm_score_${NUM_TRAJ_ANCHOR}.npy"
require_metric_cache "$WOTE_ROOT/exp/metric_cache"

symlink_count="$(find "$UTMR_ROOT" -type l | wc -l)"
printf '\nsymlinks under UTMR: %s\n' "$symlink_count"

exit "$missing"
