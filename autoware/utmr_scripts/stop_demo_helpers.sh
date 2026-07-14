#!/usr/bin/env bash
set -e

HELPER_PATTERNS=(
  pointcloud_relay.py
  straight_trajectory.py
  utmr_planner_node.py
  collision_monitor.py
  episode_metric_monitor.py
  mrm_normalizer.py
  engage_injector.py
  drive_gear_injector.py
)

for pattern in "${HELPER_PATTERNS[@]}"; do
  pkill -TERM -f "$pattern" || true
done

sleep 1.0

for pattern in "${HELPER_PATTERNS[@]}"; do
  pkill -KILL -f "$pattern" || true
done

echo "stopped UTMR helper nodes"
