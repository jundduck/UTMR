#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER_DIR="$SCRIPT_DIR/helpers"

HELPER_PATTERNS=(
  pointcloud_relay.py
  straight_trajectory.py
  utmr_planner_node.py
  collision_monitor.py
  episode_metric_monitor.py
  mrm_normalizer.py
  engage_injector.py
  drive_gear_injector.py
  route_publisher.py
  static_tf_injector.py
)

candidate_pid_dirs=()
if [[ -n "${UTMR_HELPER_PID_DIR:-}" ]]; then
  candidate_pid_dirs+=("$UTMR_HELPER_PID_DIR")
fi
if [[ -n "${UTMR_HELPER_LOG_DIR:-}" ]]; then
  candidate_pid_dirs+=("$UTMR_HELPER_LOG_DIR")
fi

kill_recorded_pid() {
  local signal_name="$1"
  local script="$2"
  local pid_file="$3"

  [[ -s "$pid_file" ]] || return 0
  local pid
  pid="$(tr -dc '0-9' <"$pid_file")"
  [[ -n "$pid" ]] || return 0
  [[ -r "/proc/$pid/cmdline" ]] || return 0

  local cmdline
  cmdline="$(tr '\0' ' ' <"/proc/$pid/cmdline")"
  if [[ "$cmdline" == *"$HELPER_DIR/$script"* ]]; then
    kill "-$signal_name" "$pid" 2>/dev/null || true
  fi
}

kill_scoped_script() {
  local signal_name="$1"
  local script="$2"
  pgrep -f "$HELPER_DIR/$script" | while read -r pid; do
    [[ -n "$pid" ]] || continue
    kill "-$signal_name" "$pid" 2>/dev/null || true
  done
}

for pattern in "${HELPER_PATTERNS[@]}"; do
  for pid_dir in "${candidate_pid_dirs[@]}"; do
    kill_recorded_pid TERM "$pattern" "$pid_dir/${pattern%.py}.pid"
  done
  kill_scoped_script TERM "$pattern"
done

sleep 1.0

for pattern in "${HELPER_PATTERNS[@]}"; do
  for pid_dir in "${candidate_pid_dirs[@]}"; do
    kill_recorded_pid KILL "$pattern" "$pid_dir/${pattern%.py}.pid"
  done
  kill_scoped_script KILL "$pattern"
done

echo "stopped UTMR helper nodes"
