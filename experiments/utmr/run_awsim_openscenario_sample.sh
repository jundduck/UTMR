#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AUTOWARE_ROOT="$ROOT/autoware"
AWSIM_ROOT="$ROOT/AWSIM-Demo/AWSIM-Demo-OpenSCENARIO"
APT_ROOT="$ROOT/runtime/apt-root"
ACADOS_ROOT="$ROOT/runtime/acados"
MISSING_ROS_PREFIX="$ROOT/runtime/ros-humble-missing/root/opt/ros/humble"
AUTOWARE_DATA_PATH="$AUTOWARE_ROOT/autoware_data/ml_models"
SCENARIO_PYTHON_PACKAGES="$ROOT/experiments/utmr/runtime/scenario-python-packages"
SCENARIO_RUNTIME_BIN="$ROOT/experiments/utmr/runtime/bin"
AUTOWARE_LOCALIZATION_CONFIG="$ROOT/experiments/utmr/config/awsim_localization"
AWSIM_SCENARIO_FRAME_RATE="${AWSIM_SCENARIO_FRAME_RATE:-5.0}"
AWSIM_GLOBAL_TIMEOUT="${AWSIM_GLOBAL_TIMEOUT:-240}"
AWSIM_INITIALIZE_DURATION="${AWSIM_INITIALIZE_DURATION:-260}"
AWSIM_PUBLISH_EMPTY_CONTEXT="${AWSIM_PUBLISH_EMPTY_CONTEXT:-true}"
AWSIM_EXTRA_STATIC_TF="${AWSIM_EXTRA_STATIC_TF:-true}"
AWSIM_DISABLE_AUTOMATIC_POSE_INITIALIZER="${AWSIM_DISABLE_AUTOMATIC_POSE_INITIALIZER:-true}"
AWSIM_FORCE_AUTONOMOUS_AFTER_READY="${AWSIM_FORCE_AUTONOMOUS_AFTER_READY:-true}"
AWSIM_FORCE_AUTONOMOUS_TIMEOUT_S="${AWSIM_FORCE_AUTONOMOUS_TIMEOUT_S:-420}"
AWSIM_FORCE_AUTONOMOUS_REPEAT_S="${AWSIM_FORCE_AUTONOMOUS_REPEAT_S:-180}"
AWSIM_READY_TOPIC_TIMEOUT_S="${AWSIM_READY_TOPIC_TIMEOUT_S:-8}"
AWSIM_EMPTY_OBJECTS_TOPIC="${AWSIM_EMPTY_OBJECTS_TOPIC:-/perception/object_recognition/objects}"
AWSIM_PUBLISH_EMPTY_OBJECTS="${AWSIM_PUBLISH_EMPTY_OBJECTS:-true}"
AWSIM_PUBLISH_EMPTY_INPUTS="${AWSIM_PUBLISH_EMPTY_INPUTS:-$AWSIM_PUBLISH_EMPTY_OBJECTS}"
AWSIM_SCENARIO="${AWSIM_SCENARIO:-}"
OUT_ROOT="${OUT_ROOT:-$ROOT/experiments/utmr/results/awsim_openscenario_sample_$(date +%Y%m%d_%H%M%S)}"
AWSIM_PLAYER_LOG="$HOME/.config/unity3d/TIERIV/AWSIM/Player.log"
HELPER_PIDS=()

cleanup_helpers() {
  local pid
  for pid in "${HELPER_PIDS[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup_helpers EXIT
trap 'cleanup_helpers; exit 130' INT TERM

mkdir -p "$OUT_ROOT/logs"

echo "UTMR root: $ROOT"
echo "Autoware root: $AUTOWARE_ROOT"
echo "AWSIM OpenSCENARIO root: $AWSIM_ROOT"
echo "Output root: $OUT_ROOT"
echo

if [[ ! -x "$AWSIM_ROOT/AWSIM-Demo-OpenSCENARIO.x86_64" ]]; then
  echo "missing executable: $AWSIM_ROOT/AWSIM-Demo-OpenSCENARIO.x86_64"
  exit 2
fi

"$ROOT/experiments/utmr/prepare_awsim_openscenario_runtime.sh"

set +u
source /opt/ros/humble/setup.bash
source "$AUTOWARE_ROOT/install/setup.bash"
set -u

prepend_env_path_if_dir() {
  local name="$1"
  local path="$2"
  if [[ -d "$path" ]]; then
    local current="${!name:-}"
    if [[ -n "$current" ]]; then
      export "$name=$path:$current"
    else
      export "$name=$path"
    fi
  fi
}

prepend_ld_path_if_dir() {
  local path="$1"
  if [[ -d "$path" ]]; then
    export LD_LIBRARY_PATH="$path:${LD_LIBRARY_PATH:-}"
  fi
}

prepend_env_path_if_dir AMENT_PREFIX_PATH "$MISSING_ROS_PREFIX"
prepend_env_path_if_dir CMAKE_PREFIX_PATH "$MISSING_ROS_PREFIX"
prepend_env_path_if_dir PYTHONPATH "$MISSING_ROS_PREFIX/local/lib/python3.10/dist-packages"
prepend_ld_path_if_dir "$APT_ROOT/usr/lib/x86_64-linux-gnu"
prepend_ld_path_if_dir "$APT_ROOT/opt/ros/humble/lib"
prepend_ld_path_if_dir "$APT_ROOT/opt/ros/humble/lib/x86_64-linux-gnu"
prepend_ld_path_if_dir "$APT_ROOT/opt/ros/humble/opt/zmqpp_vendor/lib"
prepend_ld_path_if_dir "$ACADOS_ROOT/install/lib"
prepend_ld_path_if_dir "$MISSING_ROS_PREFIX/lib"

if [[ -d "$SCENARIO_PYTHON_PACKAGES" ]]; then
  export PYTHONPATH="$SCENARIO_PYTHON_PACKAGES:${PYTHONPATH:-}"
fi
prepend_env_path_if_dir PATH "$SCENARIO_RUNTIME_BIN"

export HOME="$AUTOWARE_ROOT"
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_fastrtps_cpp}"
export FASTDDS_BUILTIN_TRANSPORTS="${FASTDDS_BUILTIN_TRANSPORTS:-UDPv4}"

require_pkg() {
  local package_name="$1"
  if ! ros2 pkg prefix "$package_name" >/dev/null 2>&1; then
    echo "missing ROS package: $package_name"
    if [[ "$package_name" == "shinjuku_map" ]]; then
      echo "Run first:"
      echo "  cd $ROOT"
      echo "  experiments/utmr/build_shinjuku_map_no_symlink.sh"
    fi
    exit 5
  fi
}

require_pkg scenario_test_runner
require_pkg scenario_simulator_v2
require_pkg shinjuku_map
require_pkg velodyne_msgs

if [[ ! -f "$AUTOWARE_DATA_PATH/lidar_centerpoint/centerpoint_tiny_ml_package.param.yaml" ]]; then
  echo "missing Autoware data path: $AUTOWARE_DATA_PATH/lidar_centerpoint/centerpoint_tiny_ml_package.param.yaml"
  exit 7
fi

if [[ ! -f "$AUTOWARE_LOCALIZATION_CONFIG/ndt_scan_matcher/ndt_scan_matcher.param.yaml" ]]; then
  echo "missing AWSIM localization config: $AUTOWARE_LOCALIZATION_CONFIG/ndt_scan_matcher/ndt_scan_matcher.param.yaml"
  exit 8
fi

echo "== package check =="
ros2 pkg prefix scenario_test_runner
ros2 pkg prefix scenario_simulator_v2
ros2 pkg prefix shinjuku_map
ros2 pkg prefix --share shinjuku_map
ros2 pkg prefix velodyne_msgs
echo "data_path: $AUTOWARE_DATA_PATH"
echo "loc_config_path: $AUTOWARE_LOCALIZATION_CONFIG"
echo "global_frame_rate: $AWSIM_SCENARIO_FRAME_RATE"
echo "global_timeout: $AWSIM_GLOBAL_TIMEOUT"
echo "initialize_duration: $AWSIM_INITIALIZE_DURATION"
echo "publish_empty_context: $AWSIM_PUBLISH_EMPTY_CONTEXT"
echo "extra_static_tf: $AWSIM_EXTRA_STATIC_TF"
echo "disable_automatic_pose_initializer: $AWSIM_DISABLE_AUTOMATIC_POSE_INITIALIZER"
echo "force_autonomous_after_ready: $AWSIM_FORCE_AUTONOMOUS_AFTER_READY"
echo "force_autonomous_timeout_s: $AWSIM_FORCE_AUTONOMOUS_TIMEOUT_S"
echo "force_autonomous_repeat_s: $AWSIM_FORCE_AUTONOMOUS_REPEAT_S"
echo "publish_empty_inputs: $AWSIM_PUBLISH_EMPTY_INPUTS"
echo "FASTDDS_BUILTIN_TRANSPORTS: $FASTDDS_BUILTIN_TRANSPORTS"
echo

echo "== AWSIM topic check =="
if ! timeout 5s ros2 topic list > "$OUT_ROOT/logs/topics_before_runner.txt"; then
  echo "failed to list ROS topics. Is ROS 2 healthy?"
  exit 3
fi

if ! grep -q '^/clock$' "$OUT_ROOT/logs/topics_before_runner.txt"; then
  echo "AWSIM does not appear to be running yet: /clock topic not found."
  echo "Start AWSIM OpenSCENARIO first:"
  echo "  cd $ROOT"
  echo "  experiments/utmr/launch_awsim_openscenario_runtime.sh"
  exit 4
fi

echo "AWSIM looks visible on ROS 2."
if [[ -f "$AWSIM_PLAYER_LOG" ]] && tail -500 "$AWSIM_PLAYER_LOG" | grep -q 'DllNotFoundException: libzmq'; then
  echo
  echo "AWSIM is visible, but its Unity log shows DllNotFoundException: libzmq."
  echo "That broken AWSIM instance cannot run OpenSCENARIO."
  echo "Close the current AWSIM window, then restart it with:"
  echo "  cd $ROOT"
  echo "  experiments/utmr/launch_awsim_openscenario_runtime.sh"
  exit 6
fi
echo

start_static_tf() {
  local label="$1"
  shift
  ros2 run tf2_ros static_transform_publisher "$@" \
    > "$OUT_ROOT/logs/static_tf_${label}.log" 2>&1 &
  HELPER_PIDS+=("$!")
}

start_empty_inputs() {
  AWSIM_EMPTY_SIMULATION_GUARD=1 \
    AWSIM_EMPTY_PUBLISH_PERCEPTION=true \
    AWSIM_EMPTY_PUBLISH_EMERGENCY=true \
    AWSIM_EMPTY_PUBLISH_MRM_STATE="${AWSIM_EMPTY_PUBLISH_MRM_STATE:-true}" \
    python3 "$ROOT/autoware/utmr_scripts/helpers/empty_sim_inputs.py" \
    > "$OUT_ROOT/logs/empty_sim_inputs.log" 2>&1 &
  HELPER_PIDS+=("$!")
}

watch_automatic_pose_initializer() {
  local deadline=$((SECONDS + ${AWSIM_AUTOMATIC_POSE_INITIALIZER_WATCH_S:-300}))
  while [[ "$SECONDS" -lt "$deadline" ]]; do
    while read -r pid cmd; do
      if [[ "$cmd" == *autoware_automatic_pose_initializer_node* ]]; then
        kill -TERM "$pid" 2>/dev/null || true
        printf 'disabled autoware automatic pose initializer pid=%s\n' "$pid"
      fi
    done < <(ps -eo pid=,cmd=)
    sleep 0.1
  done
}

wait_for_topic_once() {
  local topic="$1"
  local timeout_s="$2"
  timeout "${timeout_s}s" ros2 topic echo "$topic" --once >/dev/null 2>&1
}

force_autonomous_after_ready() {
  local deadline=$((SECONDS + AWSIM_FORCE_AUTONOMOUS_TIMEOUT_S))
  local saw_localization=0
  local saw_route=0
  local saw_trajectory=0

  echo "waiting for /localization/kinematic_state, /planning/mission_planning/route, and /planning/trajectory"
  while [[ "$SECONDS" -lt "$deadline" ]]; do
    if [[ "$saw_localization" -eq 0 ]] &&
      wait_for_topic_once /localization/kinematic_state "$AWSIM_READY_TOPIC_TIMEOUT_S"; then
      saw_localization=1
      echo "ready: /localization/kinematic_state"
    fi

    if [[ "$saw_route" -eq 0 ]] &&
      wait_for_topic_once /planning/mission_planning/route "$AWSIM_READY_TOPIC_TIMEOUT_S"; then
      saw_route=1
      echo "ready: /planning/mission_planning/route"
    fi

    if [[ "$saw_trajectory" -eq 0 ]] &&
      wait_for_topic_once /planning/trajectory "$AWSIM_READY_TOPIC_TIMEOUT_S"; then
      saw_trajectory=1
      echo "ready: /planning/trajectory"
    fi

    if [[ "$saw_localization" -eq 1 && "$saw_route" -eq 1 && "$saw_trajectory" -eq 1 ]]; then
      break
    fi
    sleep 1
  done

  if [[ "$saw_localization" -ne 1 || "$saw_route" -ne 1 || "$saw_trajectory" -ne 1 ]]; then
    echo "timeout while waiting for ready topics"
    return 0
  fi

  local repeat_deadline=$((SECONDS + AWSIM_FORCE_AUTONOMOUS_REPEAT_S))
  while [[ "$SECONDS" -lt "$repeat_deadline" ]]; do
    date '+%Y-%m-%dT%H:%M:%S%z'
    timeout 5s ros2 service call /api/autoware/set/emergency \
      tier4_external_api_msgs/srv/SetEmergency \
      "{emergency: false}" || true
    timeout 5s ros2 service call /control/vehicle_cmd_gate/clear_external_emergency_stop \
      std_srvs/srv/Trigger \
      "{}" || true
    timeout 5s ros2 service call /control/vehicle_cmd_gate/set_stop \
      tier4_control_msgs/srv/SetStop \
      "{stop: false, request_source: utmr_openscenario_auto}" || true
    timeout 5s ros2 service call /system/operation_mode/change_autoware_control \
      autoware_system_msgs/srv/ChangeAutowareControl \
      "{autoware_control: true}" || true
    timeout 5s ros2 service call /system/operation_mode/change_operation_mode \
      autoware_system_msgs/srv/ChangeOperationMode \
      "{mode: 2}" || true
    timeout 5s ros2 topic echo /api/operation_mode/state --once || true
    timeout 5s ros2 topic echo /control/command/control_cmd --once || true
    sleep 3
  done
}

if [[ "$AWSIM_EXTRA_STATIC_TF" == "true" ]]; then
  start_static_tf base_to_sensor \
    --x 0.9 --y 0.0 --z 2.0 --roll -0.001 --pitch 0.015 --yaw -0.0364 \
    --frame-id base_link --child-frame-id sensor_kit_base_link
  start_static_tf sensor_to_imu \
    --x 0.0 --y 0.0 --z 0.0 --roll 3.14159265359 --pitch 0.0 --yaw 3.14159265359 \
    --frame-id sensor_kit_base_link --child-frame-id tamagawa/imu_link
  start_static_tf sensor_to_gnss \
    --x -0.1 --y 0.0 --z -0.2 --roll 0.0 --pitch 0.0 --yaw 0.0 \
    --frame-id sensor_kit_base_link --child-frame-id gnss_link
  start_static_tf sensor_to_lidar_base \
    --x 0.0 --y 0.0 --z 0.0 --roll 0.0 --pitch 0.0 --yaw 1.575 \
    --frame-id sensor_kit_base_link --child-frame-id velodyne_top_base_link
  start_static_tf lidar_base_to_lidar \
    --x 0.0 --y 0.0 --z 0.0377 --roll 0.0 --pitch 0.0 --yaw 0.0 \
    --frame-id velodyne_top_base_link --child-frame-id velodyne_top
fi

if [[ "$AWSIM_PUBLISH_EMPTY_INPUTS" == "true" ]]; then
  start_empty_inputs
fi

if [[ "$AWSIM_DISABLE_AUTOMATIC_POSE_INITIALIZER" == "true" ]]; then
  watch_automatic_pose_initializer > "$OUT_ROOT/logs/automatic_pose_initializer_watch.log" 2>&1 &
  HELPER_PIDS+=("$!")
fi

if [[ -n "$AWSIM_SCENARIO" ]]; then
  SCENARIO="$AWSIM_SCENARIO"
else
  SCENARIO="$(ros2 pkg prefix --share scenario_test_runner)/scenario/sample_awsim.yaml"
fi

if [[ "$AWSIM_FORCE_AUTONOMOUS_AFTER_READY" == "true" ]]; then
  force_autonomous_after_ready > "$OUT_ROOT/logs/force_autonomous.log" 2>&1 &
  HELPER_PIDS+=("$!")
fi

echo "== running scenario_test_runner =="
echo "scenario: $SCENARIO"
echo "log: $OUT_ROOT/logs/scenario_test_runner.log"

ros2 launch scenario_test_runner scenario_test_runner.launch.py \
  architecture_type:=awf/universe/20250130 \
  record:=false \
  scenario:="$SCENARIO" \
  sensor_model:=awsim_sensor_kit \
  vehicle_model:=sample_vehicle \
  launch_simple_sensor_simulator:=false \
  global_frame_rate:="$AWSIM_SCENARIO_FRAME_RATE" \
  global_timeout:="$AWSIM_GLOBAL_TIMEOUT" \
  publish_empty_context:="$AWSIM_PUBLISH_EMPTY_CONTEXT" \
  autoware_launch_file:="e2e_simulator.launch.xml" \
  launch_visualization:=false \
  autoware.rviz:=false \
  autoware.rviz_respawn:=false \
  autoware.perception:=false \
  autoware.launch_perception:=false \
  autoware.use_pointcloud_container:=false \
  autoware.loc_config_path:="$AUTOWARE_LOCALIZATION_CONFIG" \
  data_path:="$AUTOWARE_DATA_PATH" \
  initialize_duration:="$AWSIM_INITIALIZE_DURATION" \
  port:=8080 \
  2>&1 | tee "$OUT_ROOT/logs/scenario_test_runner.log"

echo
echo "== relevant result lines =="
grep -Ei "success|failure|collision|exitSuccess|exitFailure|timeout|error|exception" \
  "$OUT_ROOT/logs/scenario_test_runner.log" | tail -80 || true

echo
echo "done: $OUT_ROOT"
