#!/usr/bin/env bash
set -e

export FASTDDS_BUILTIN_TRANSPORTS="${FASTDDS_BUILTIN_TRANSPORTS:-UDPv4}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UTMR_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HELPER_DIR="$SCRIPT_DIR/helpers"
AUTOWARE_DIR="${AUTOWARE_DIR:-$UTMR_ROOT/autoware}"
UTMR_SERVICE_LIST_TIMEOUT_S="${UTMR_SERVICE_LIST_TIMEOUT_S:-8}"
UTMR_SERVICE_CALL_TIMEOUT_S="${UTMR_SERVICE_CALL_TIMEOUT_S:-15}"

source /opt/ros/humble/setup.bash
source "$SCRIPT_DIR/setup_runtime_overlay.sh"
cd "$AUTOWARE_DIR"
source install/setup.bash

start_helper() {
  local name="$1"
  local script="$2"
  local log="$3"

  if pgrep -f "$script" >/dev/null; then
    echo "$name already running"
  else
    nohup python3 "$script" >"$log" 2>&1 &
    echo "$name started pid=$!"
  fi
}

start_helper pointcloud_relay "$HELPER_DIR/pointcloud_relay.py" /tmp/utmr-pointcloud-relay.log
start_helper mrm_normalizer "$HELPER_DIR/mrm_normalizer.py" /tmp/utmr-mrm-normalizer.log
start_helper engage_injector "$HELPER_DIR/engage_injector.py" /tmp/utmr-engage-injector.log
start_helper drive_gear_injector "$HELPER_DIR/drive_gear_injector.py" /tmp/utmr-drive-gear-injector.log

echo "waiting for Autoware services..."
sleep 3

service_exists() {
  timeout "$UTMR_SERVICE_LIST_TIMEOUT_S" ros2 service list 2>/tmp/utmr-ros2-service-list.err | grep -qx "$1"
}

if service_exists /localization/initialize; then
  timeout "$UTMR_SERVICE_CALL_TIMEOUT_S" ros2 service call /localization/initialize autoware_localization_msgs/srv/InitializeLocalization "{method: 1, pose_with_covariance: [{header: {frame_id: map}, pose: {pose: {position: {x: 81377.0, y: 49917.0, z: 41.3}, orientation: {x: 0.00047656021952033887, y: -0.005042256930040435, z: 0.29035442036333825, w: 0.9569057733710663}}, covariance: [0.25, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.25, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.25, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0685, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0685, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0685]}}]}" || true
else
  echo "skip localization initialize: service is not available yet"
fi

if service_exists /api/routing/set_route_points; then
  timeout "$UTMR_SERVICE_CALL_TIMEOUT_S" ros2 service call /api/routing/set_route_points autoware_adapi_v1_msgs/srv/SetRoutePoints "{header: {frame_id: map}, option: {allow_goal_modification: true}, goal: {position: {x: 81393.98, y: 49928.02, z: 41.27}, orientation: {x: 0.00047656021952033887, y: -0.005042256930040435, z: 0.29035442036333825, w: 0.9569057733710663}}, waypoints: []}" || true
fi

if service_exists /system/operation_mode/change_operation_mode; then
  timeout "$UTMR_SERVICE_CALL_TIMEOUT_S" ros2 service call /system/operation_mode/change_operation_mode autoware_system_msgs/srv/ChangeOperationMode "{mode: 2}" || true
fi

if service_exists /control/vehicle_cmd_gate/set_stop; then
  timeout "$UTMR_SERVICE_CALL_TIMEOUT_S" ros2 service call /control/vehicle_cmd_gate/set_stop tier4_control_msgs/srv/SetStop "{stop: false, request_source: utmr}" || true
fi

start_helper straight_trajectory "$HELPER_DIR/straight_trajectory.py" /tmp/utmr-straight-trajectory.log

echo "done. Watch /vehicle/status/gear_status, /control/command/control_cmd, /localization/kinematic_state."
