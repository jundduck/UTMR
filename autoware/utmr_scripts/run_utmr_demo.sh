#!/usr/bin/env bash
set -e

export FASTDDS_BUILTIN_TRANSPORTS="${FASTDDS_BUILTIN_TRANSPORTS:-UDPv4}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UTMR_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HELPER_DIR="$SCRIPT_DIR/helpers"
AUTOWARE_DIR="${AUTOWARE_DIR:-$UTMR_ROOT/autoware}"
UTMR_STEP_LOG="${UTMR_STEP_LOG:-$UTMR_ROOT/experiments/utmr/results/awsim_live/raw/utmr_steps.jsonl}"
UTMR_MODE="${UTMR_MODE:-utmr}"
UTMR_INIT_X="${UTMR_INIT_X:-81377.0}"
UTMR_INIT_Y="${UTMR_INIT_Y:-49917.0}"
UTMR_INIT_Z="${UTMR_INIT_Z:-41.3}"
UTMR_INIT_QX="${UTMR_INIT_QX:-0.00047656021952033887}"
UTMR_INIT_QY="${UTMR_INIT_QY:--0.005042256930040435}"
UTMR_INIT_QZ="${UTMR_INIT_QZ:-0.29035442036333825}"
UTMR_INIT_QW="${UTMR_INIT_QW:-0.9569057733710663}"
UTMR_INIT_COVARIANCE="${UTMR_INIT_COVARIANCE:-[0.25, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.25, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.25, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0685, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0685, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0685]}"
UTMR_GOAL_X="${UTMR_GOAL_X:-81393.98}"
UTMR_GOAL_Y="${UTMR_GOAL_Y:-49928.02}"
UTMR_GOAL_Z="${UTMR_GOAL_Z:-41.27}"
UTMR_GOAL_QX="${UTMR_GOAL_QX:-0.00047656021952033887}"
UTMR_GOAL_QY="${UTMR_GOAL_QY:--0.005042256930040435}"
UTMR_GOAL_QZ="${UTMR_GOAL_QZ:-0.29035442036333825}"
UTMR_GOAL_QW="${UTMR_GOAL_QW:-0.9569057733710663}"
UTMR_ROUTE_LENGTH_M="${UTMR_ROUTE_LENGTH_M:-20.25}"
UTMR_FALLBACK_X="${UTMR_FALLBACK_X:-$UTMR_INIT_X}"
UTMR_FALLBACK_Y="${UTMR_FALLBACK_Y:-$UTMR_INIT_Y}"
UTMR_FALLBACK_Z="${UTMR_FALLBACK_Z:-$UTMR_INIT_Z}"
UTMR_FALLBACK_YAW="${UTMR_FALLBACK_YAW:-0.5895}"
UTMR_COLLISION_TOPIC="${UTMR_COLLISION_TOPIC:-/utmr/collision}"
UTMR_COLLISION_OUTPUT_TOPIC="${UTMR_COLLISION_OUTPUT_TOPIC:-/utmr/collision}"
UTMR_SERVICE_INITIAL_WAIT_S="${UTMR_SERVICE_INITIAL_WAIT_S:-3}"
UTMR_SERVICE_LIST_TIMEOUT_S="${UTMR_SERVICE_LIST_TIMEOUT_S:-3}"
UTMR_SERVICE_CALL_TIMEOUT_S="${UTMR_SERVICE_CALL_TIMEOUT_S:-6}"
UTMR_SERVICE_RETRY_COUNT="${UTMR_SERVICE_RETRY_COUNT:-4}"
UTMR_SERVICE_RETRY_SLEEP_S="${UTMR_SERVICE_RETRY_SLEEP_S:-2}"
UTMR_SERVICE_RESPONSE_DIR="${UTMR_SERVICE_RESPONSE_DIR:-${TMPDIR:-/tmp}}"
UTMR_ROUTE_UUID_BYTES="${UTMR_ROUTE_UUID_BYTES:-[17,17,17,17,34,34,51,51,68,68,85,85,85,85,85,85]}"
export UTMR_MODE
export UTMR_STEP_LOG
export UTMR_GOAL_X
export UTMR_GOAL_Y
export UTMR_ROUTE_LENGTH_M
export UTMR_FALLBACK_X
export UTMR_FALLBACK_Y
export UTMR_FALLBACK_Z
export UTMR_FALLBACK_YAW
export UTMR_COLLISION_TOPIC
export UTMR_COLLISION_OUTPUT_TOPIC
export UTMR_INIT_X
export UTMR_INIT_Y
export UTMR_INIT_Z
export UTMR_INIT_QX
export UTMR_INIT_QY
export UTMR_INIT_QZ
export UTMR_INIT_QW
export UTMR_GOAL_Z
export UTMR_GOAL_QX
export UTMR_GOAL_QY
export UTMR_GOAL_QZ
export UTMR_GOAL_QW

source /opt/ros/humble/setup.bash
source "$SCRIPT_DIR/service_calls.sh"
source "$SCRIPT_DIR/service_readiness.sh"
source "$SCRIPT_DIR/setup_runtime_overlay.sh"
cd "$AUTOWARE_DIR"
source install/setup.bash

start_helper() {
  local name="$1"
  local script="$2"
  local log="$3"
  local pid_dir="${UTMR_HELPER_PID_DIR:-${UTMR_HELPER_LOG_DIR:-}}"
  local pid_file=""
  if [[ -n "$pid_dir" ]]; then
    mkdir -p "$pid_dir"
    pid_file="$pid_dir/$name.pid"
  fi

  if pgrep -f "$script" >/dev/null; then
    echo "$name already running"
  else
    nohup python3 "$script" >"$log" 2>&1 &
    local pid="$!"
    if [[ -n "$pid_file" ]]; then
      printf '%s\n' "$pid" >"$pid_file"
    fi
    echo "$name started pid=$pid"
  fi
}

helper_log() {
  local file_name="$1"
  if [[ -n "${UTMR_HELPER_LOG_DIR:-}" ]]; then
    mkdir -p "$UTMR_HELPER_LOG_DIR"
    printf '%s/%s\n' "$UTMR_HELPER_LOG_DIR" "$file_name"
  else
    printf '/tmp/%s\n' "$file_name"
  fi
}

start_helper pointcloud_relay "$HELPER_DIR/pointcloud_relay.py" "$(helper_log utmr-pointcloud-relay.log)"
if [[ "${UTMR_START_STATIC_TF_INJECTOR:-1}" != "0" ]]; then
  start_helper static_tf_injector "$HELPER_DIR/static_tf_injector.py" "$(helper_log utmr-static-tf-injector.log)"
fi
start_helper mrm_normalizer "$HELPER_DIR/mrm_normalizer.py" "$(helper_log utmr-mrm-normalizer.log)"
start_helper engage_injector "$HELPER_DIR/engage_injector.py" "$(helper_log utmr-engage-injector.log)"
start_helper drive_gear_injector "$HELPER_DIR/drive_gear_injector.py" "$(helper_log utmr-drive-gear-injector.log)"

if [[ "${UTMR_START_ROUTE_PUBLISHER:-1}" != "0" ]]; then
  start_helper route_publisher "$HELPER_DIR/route_publisher.py" "$(helper_log utmr-route-publisher.log)"
fi

if [[ "${UTMR_START_COLLISION_MONITOR:-1}" != "0" ]]; then
  start_helper collision_monitor "$HELPER_DIR/collision_monitor.py" "$(helper_log utmr-collision-monitor.log)"
fi

if [[ "${UTMR_START_METRIC_MONITOR:-1}" != "0" && -n "${UTMR_EPISODE_CSV:-}" ]]; then
  start_helper episode_metric_monitor "$HELPER_DIR/episode_metric_monitor.py" "$(helper_log utmr-episode-metric-monitor.log)"
fi

mkdir -p "$(dirname "$UTMR_STEP_LOG")"
start_helper utmr_planner "$HELPER_DIR/utmr_planner_node.py" "$(helper_log utmr-planner-node.log)"

echo "waiting for Autoware services..."
sleep "$UTMR_SERVICE_INITIAL_WAIT_S"

INIT_REQUEST="{method: 1, pose_with_covariance: [{header: {frame_id: map}, pose: {pose: {position: {x: $UTMR_INIT_X, y: $UTMR_INIT_Y, z: $UTMR_INIT_Z}, orientation: {x: $UTMR_INIT_QX, y: $UTMR_INIT_QY, z: $UTMR_INIT_QZ, w: $UTMR_INIT_QW}}, covariance: $UTMR_INIT_COVARIANCE}}]}"
ROUTE_REQUEST="{header: {frame_id: map}, option: {allow_goal_modification: true}, goal: {position: {x: $UTMR_GOAL_X, y: $UTMR_GOAL_Y, z: $UTMR_GOAL_Z}, orientation: {x: $UTMR_GOAL_QX, y: $UTMR_GOAL_QY, z: $UTMR_GOAL_QZ, w: $UTMR_GOAL_QW}}, waypoints: []}"
WAYPOINT_ROUTE_REQUEST="{header: {frame_id: map}, goal_pose: {position: {x: $UTMR_GOAL_X, y: $UTMR_GOAL_Y, z: $UTMR_GOAL_Z}, orientation: {x: $UTMR_GOAL_QX, y: $UTMR_GOAL_QY, z: $UTMR_GOAL_QZ, w: $UTMR_GOAL_QW}}, waypoints: [], uuid: {uuid: $UTMR_ROUTE_UUID_BYTES}, allow_modification: true}"
utmr_run_readiness_sequence "$INIT_REQUEST" "$ROUTE_REQUEST" "$WAYPOINT_ROUTE_REQUEST"

if utmr_readiness_success; then
  echo "done. UTMR_READY=1 planner mode=$UTMR_MODE, step log=$UTMR_STEP_LOG"
else
  echo "degraded. UTMR_READY=0 localization_ready=$localization_ready route_ready=$route_ready operation_ready=$operation_ready gate_ready=$gate_ready planner mode=$UTMR_MODE, step log=$UTMR_STEP_LOG"
  exit 2
fi
