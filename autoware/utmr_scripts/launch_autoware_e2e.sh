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

source /opt/ros/humble/setup.bash
source "$SCRIPT_DIR/setup_runtime_overlay.sh"
cd "$AUTOWARE_DIR"
source install/setup.bash

ros2 launch autoware_launch e2e_simulator.launch.xml \
  vehicle_model:=sample_vehicle \
  sensor_model:=awsim_sensor_kit \
  map_path:="$MAP_PATH" \
  data_path:="$DATA_PATH" \
  rviz:="$RVIZ" \
  perception:="$PERCEPTION" \
  planning:="$PLANNING"
