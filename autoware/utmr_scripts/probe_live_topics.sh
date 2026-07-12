#!/usr/bin/env bash
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UTMR_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
AUTOWARE_DIR="${AUTOWARE_DIR:-$UTMR_ROOT/autoware}"

source /opt/ros/humble/setup.bash
source "$SCRIPT_DIR/setup_runtime_overlay.sh"
cd "$AUTOWARE_DIR"
source install/setup.bash

TOPIC_LIST_TIMEOUT_S="${UTMR_TOPIC_LIST_TIMEOUT_S:-10}"

echo "# Object / collision / route / localization topics"
topics="$(timeout "$TOPIC_LIST_TIMEOUT_S" ros2 topic list -t 2>/tmp/utmr-topic-probe.err || true)"
printf '%s\n' "$topics" | rg "object|perception|collision|route|routing|kinematic|velocity|hazard|diagnostic|contact|crash" || true

echo
echo "# Services useful for episode setup"
timeout "$TOPIC_LIST_TIMEOUT_S" ros2 service list 2>/tmp/utmr-service-probe.err | rg "localization|routing|operation_mode|vehicle_cmd_gate" || true

pick_topic() {
  local type_name="$1"
  printf '%s\n' "$topics" | awk -v type_name="$type_name" '$0 ~ type_name {print $1; exit}'
}

object_topic="$(pick_topic 'autoware_perception_msgs/msg/PredictedObjects')"
object_type="PredictedObjects"
if [[ -z "$object_topic" ]]; then
  object_topic="$(pick_topic 'autoware_perception_msgs/msg/TrackedObjects')"
  object_type="TrackedObjects"
fi
if [[ -z "$object_topic" ]]; then
  object_topic="$(pick_topic 'autoware_perception_msgs/msg/DetectedObjects')"
  object_type="DetectedObjects"
fi

collision_topic="$(printf '%s\n' "$topics" | awk '/std_msgs\/msg\/Bool/ && /collision|contact|crash|hazard/ {print $1; exit}')"

echo
echo "# Suggested UTMR environment"
if [[ -n "$object_topic" ]]; then
  echo "export UTMR_OBJECTS_TOPIC=$object_topic"
  echo "export UTMR_OBJECTS_MSG_TYPE=$object_type"
else
  echo "# no Autoware object topic found; keep UTMR_OBJECTS_TOPIC unset or publish /utmr/obstacles_json"
fi

if [[ -n "$collision_topic" ]]; then
  echo "export UTMR_COLLISION_TOPIC=$collision_topic"
else
  echo "export UTMR_COLLISION_TOPIC=/utmr/collision"
  echo "# /utmr/collision is provided by autoware/utmr_scripts/helpers/collision_monitor.py"
fi
