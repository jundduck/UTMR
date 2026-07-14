#!/usr/bin/env bash
set -e

export FASTDDS_BUILTIN_TRANSPORTS="${FASTDDS_BUILTIN_TRANSPORTS:-UDPv4}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UTMR_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

AWSIM_DIR="${AWSIM_DIR:-$UTMR_ROOT/AWSIM-Demo}"
AUTOWARE_DIR="${AUTOWARE_DIR:-$UTMR_ROOT/autoware}"
AUTOWARE_DATA_DIR="${AUTOWARE_DATA_DIR:-$AUTOWARE_DIR/autoware_data}"
MAP_PATH="${MAP_PATH:-$AWSIM_DIR/Shinjuku-Map/map}"
DATA_PATH="${DATA_PATH:-$AUTOWARE_DATA_DIR/ml_models}"
RVIZ="${RVIZ:-false}"
PERCEPTION="${PERCEPTION:-false}"
PLANNING="${PLANNING:-false}"
UTMR_DISABLE_AUTOMATIC_POSE_INITIALIZER="${UTMR_DISABLE_AUTOMATIC_POSE_INITIALIZER:-0}"

watch_automatic_pose_initializer() {
  local deadline=$((SECONDS + ${UTMR_AUTOMATIC_POSE_INITIALIZER_WATCH_S:-60}))
  while [[ "$SECONDS" -lt "$deadline" ]]; do
    while read -r pid cmd; do
      if [[ "$cmd" == *autoware_automatic_pose_initializer_node* ]]; then
        kill -TERM "$pid" 2>/dev/null || true
        echo "disabled autoware automatic pose initializer pid=$pid"
      fi
    done < <(ps -eo pid=,cmd=)
    sleep 0.1
  done
}

source /opt/ros/humble/setup.bash
source "$SCRIPT_DIR/setup_runtime_overlay.sh"
cd "$AUTOWARE_DIR"
source install/setup.bash

WATCHER_PID=""
if [[ "$UTMR_DISABLE_AUTOMATIC_POSE_INITIALIZER" == "1" ]]; then
  watch_automatic_pose_initializer &
  WATCHER_PID="$!"
  trap '[[ -n "$WATCHER_PID" ]] && kill "$WATCHER_PID" 2>/dev/null || true' EXIT
fi

ros2 launch autoware_launch e2e_simulator.launch.xml \
  vehicle_model:=sample_vehicle \
  sensor_model:=awsim_sensor_kit \
  map_path:="$MAP_PATH" \
  data_path:="$DATA_PATH" \
  rviz:="$RVIZ" \
  perception:="$PERCEPTION" \
  planning:="$PLANNING"
