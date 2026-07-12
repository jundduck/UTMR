#!/usr/bin/env bash
set -e

pkill -f pointcloud_relay.py || true
pkill -f straight_trajectory.py || true
pkill -f utmr_planner_node.py || true
pkill -f collision_monitor.py || true
pkill -f episode_metric_monitor.py || true
pkill -f mrm_normalizer.py || true
pkill -f engage_injector.py || true
pkill -f drive_gear_injector.py || true

echo "stopped UTMR helper nodes"
