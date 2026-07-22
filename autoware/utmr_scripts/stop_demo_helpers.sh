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
  empty_sim_inputs.py
)

candidate_pid_dirs=()
if [[ -n "${UTMR_HELPER_PID_DIR:-}" ]]; then
  candidate_pid_dirs+=("$UTMR_HELPER_PID_DIR")
fi
if [[ -n "${UTMR_HELPER_LOG_DIR:-}" ]]; then
  candidate_pid_dirs+=("$UTMR_HELPER_LOG_DIR")
fi

pid_matches_helper() {
  local pid="$1"
  local script="$2"

  [[ "$pid" != "$$" ]] || return 1
  [[ -r "/proc/$pid/cmdline" ]] || return 1

  local -a args=()
  mapfile -d '' -t args <"/proc/$pid/cmdline" || return 1
  [[ "${#args[@]}" -gt 0 ]] || return 1

  local helper_path="$HELPER_DIR/$script"
  local arg
  local has_helper=0
  for arg in "${args[@]}"; do
    if [[ "$arg" == "$helper_path" ]]; then
      has_helper=1
      break
    fi
  done
  [[ "$has_helper" == "1" ]] || return 1

  case "${args[0]}" in
    *python*|"$helper_path")
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

kill_recorded_pid() {
  local signal_name="$1"
  local script="$2"
  local pid_file="$3"

  [[ -s "$pid_file" ]] || return 0
  local pid
  pid="$(tr -dc '0-9' <"$pid_file")"
  [[ -n "$pid" ]] || return 0

  if pid_matches_helper "$pid" "$script"; then
    kill "-$signal_name" "$pid" 2>/dev/null || true
  fi
}

kill_scoped_script() {
  local signal_name="$1"
  local script="$2"
  pgrep -f "$HELPER_DIR/$script" | while read -r pid; do
    [[ -n "$pid" ]] || continue
    if pid_matches_helper "$pid" "$script"; then
      kill "-$signal_name" "$pid" 2>/dev/null || true
    fi
  done
}

first_signal="${UTMR_HELPER_STOP_SIGNAL:-INT}"
allow_global_stop="${UTMR_HELPER_ALLOW_GLOBAL_STOP:-0}"

for pattern in "${HELPER_PATTERNS[@]}"; do
  for pid_dir in "${candidate_pid_dirs[@]}"; do
    kill_recorded_pid "$first_signal" "$pattern" "$pid_dir/${pattern%.py}.pid"
  done
  if [[ "$allow_global_stop" == "1" || "$allow_global_stop" == "true" ]]; then
    kill_scoped_script "$first_signal" "$pattern"
  fi
done

sleep "${UTMR_HELPER_TERM_WAIT_S:-3.0}"

for pattern in "${HELPER_PATTERNS[@]}"; do
  for pid_dir in "${candidate_pid_dirs[@]}"; do
    kill_recorded_pid KILL "$pattern" "$pid_dir/${pattern%.py}.pid"
  done
  if [[ "$allow_global_stop" == "1" || "$allow_global_stop" == "true" ]]; then
    kill_scoped_script KILL "$pattern"
  fi
done

echo "stopped UTMR helper nodes"
