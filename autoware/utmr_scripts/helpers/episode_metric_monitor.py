#!/usr/bin/env python3
import csv
import math
import os
import signal
import time
from pathlib import Path

import rclpy
from rclpy._rclpy_pybind11 import RCLError
from autoware_adapi_v1_msgs.msg import RouteState
from autoware_localization_msgs.msg import KinematicState
from helper_shutdown import is_expected_shutdown_error
from nav_msgs.msg import Odometry
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.parameter import Parameter
from rosidl_generator_py.import_type_support_impl import UnsupportedTypeSupport
from std_msgs.msg import Bool


class EpisodeMetricMonitor(Node):
    def __init__(self):
        super().__init__("utmr_episode_metric_monitor")
        self.set_parameters([Parameter("use_sim_time", Parameter.Type.BOOL, True)])

        self.episode_csv = os.environ.get("UTMR_EPISODE_CSV", "")
        self.method = os.environ.get("UTMR_METHOD", "WoTE + UTMR (Ours)")
        self.variant = os.environ.get("UTMR_VARIANT", os.environ.get("UTMR_MODE", "utmr"))
        self.episode_id = os.environ.get("UTMR_EPISODE_ID", f"{self.variant}_{int(time.time())}")
        self.warmup_s = float(os.environ.get("UTMR_METRIC_WARMUP_S", "2.0"))
        self.goal_radius_m = float(os.environ.get("UTMR_GOAL_RADIUS_M", "5.0"))
        self.goal_x = optional_float(os.environ.get("UTMR_GOAL_X", ""))
        self.goal_y = optional_float(os.environ.get("UTMR_GOAL_Y", ""))
        self.route_length_m = optional_float(os.environ.get("UTMR_ROUTE_LENGTH_M", ""))
        self.collision_topic = os.environ.get("UTMR_COLLISION_TOPIC", "")
        self.has_collision_topic = bool(self.collision_topic)
        self.assume_timeout_on_stop = env_bool(os.environ.get("UTMR_METRIC_ASSUME_TIMEOUT_ON_STOP", ""), False)
        self.kinematic_topic = os.environ.get("UTMR_KINEMATIC_TOPIC", "/localization/kinematic_state")
        self.kinematic_msg_type = os.environ.get("UTMR_KINEMATIC_MSG_TYPE", "KinematicState")
        self.route_state_topic = os.environ.get("UTMR_ROUTE_STATE_TOPIC", "/api/routing/state")

        self.started_wall = time.monotonic()
        self.first_pose = None
        self.last_pose = None
        self.speed_samples_kmh = []
        self.collision = False
        self.success = False
        self.route_state_enabled = False
        self.row_written = False
        self.dropped_uninitialized_samples = 0

        self.create_kinematic_subscription()
        self.create_route_state_subscription()
        if self.collision_topic:
            self.create_subscription(Bool, self.collision_topic, self.on_collision, 10)
            self.get_logger().info(f"subscribed collision topic {self.collision_topic}")
        self.get_logger().info(f"metric monitor episode={self.episode_id} csv={self.episode_csv or '(disabled)'}")

    def create_kinematic_subscription(self):
        if self.kinematic_msg_type == "KinematicState":
            self.create_subscription(KinematicState, self.kinematic_topic, self.on_kinematic_state, 10)
        else:
            if self.kinematic_msg_type != "Odometry":
                self.get_logger().warning(f"unknown UTMR_KINEMATIC_MSG_TYPE={self.kinematic_msg_type}; using Odometry")
            self.create_subscription(Odometry, self.kinematic_topic, self.on_odometry, 10)
        self.get_logger().info(f"subscribed kinematic topic {self.kinematic_topic} as {self.kinematic_msg_type}")

    def create_route_state_subscription(self):
        try:
            self.create_subscription(RouteState, self.route_state_topic, self.on_route_state, 10)
            self.route_state_enabled = True
            self.get_logger().info(f"subscribed route state topic {self.route_state_topic}")
        except UnsupportedTypeSupport as exc:
            self.route_state_enabled = False
            self.get_logger().warning(
                f"route state subscription disabled because type support is unavailable: {exc}"
            )

    def on_kinematic_state(self, msg: KinematicState):
        pose = msg.pose_with_covariance.pose
        twist = msg.twist_with_covariance.twist
        self.record_motion(pose, twist)

    def on_odometry(self, msg: Odometry):
        pose = msg.pose.pose
        twist = msg.twist.twist
        self.record_motion(pose, twist)

    def record_motion(self, pose, twist):
        current = (pose.position.x, pose.position.y)
        if self.is_uninitialized_pose(current):
            self.dropped_uninitialized_samples += 1
            if self.dropped_uninitialized_samples == 1:
                self.get_logger().info(
                    "dropping kinematic samples until scenario pose is near the configured route"
                )
            return
        if self.first_pose is None:
            self.first_pose = current
        self.last_pose = current

        elapsed = time.monotonic() - self.started_wall
        speed_kmh = math.hypot(twist.linear.x, twist.linear.y) * 3.6
        if elapsed >= self.warmup_s:
            self.speed_samples_kmh.append(speed_kmh)

        if self.goal_x is not None and self.goal_y is not None:
            if math.hypot(pose.position.x - self.goal_x, pose.position.y - self.goal_y) <= self.goal_radius_m:
                self.success = True

    def on_route_state(self, msg: RouteState):
        if msg.state == RouteState.ARRIVED:
            self.success = True

    def on_collision(self, msg: Bool):
        self.collision = self.collision or bool(msg.data)

    def write_row(self):
        if self.row_written or not self.episode_csv:
            return
        self.row_written = True

        distance = self.distance_m()
        mean_speed = mean(self.speed_samples_kmh)
        driving_score = self.driving_score(distance, mean_speed)
        has_motion_samples = self.first_pose is not None and self.last_pose is not None
        metric_source = "observed" if has_motion_samples else "fallback"
        notes = []
        if not self.route_state_enabled:
            notes.append("route_state_subscription_unavailable")
        if not self.has_collision_topic:
            notes.append("collision_topic_unavailable")
        if not self.assume_timeout_on_stop and not self.success and not self.collision:
            notes.append("timeout_unclassified")
        if not has_motion_samples:
            notes.append("no_kinematic_samples")
        if self.dropped_uninitialized_samples:
            notes.append(f"dropped_uninitialized_samples={self.dropped_uninitialized_samples}")
        collision_value = self.collision if self.has_collision_topic else ""
        timeout_value = self.timeout_value()
        path = Path(self.episode_csv)
        path.parent.mkdir(parents=True, exist_ok=True)
        exists = path.exists()
        fieldnames = [
            "method",
            "variant",
            "episode_id",
            "collision",
            "success",
            "timeout",
            "distance_m",
            "route_length_m",
            "mean_speed_kmh",
            "driving_score",
            "metric_source",
            "metric_note",
        ]
        with path.open("a", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=fieldnames)
            if not exists:
                writer.writeheader()
            writer.writerow(
                {
                    "method": self.method,
                    "variant": self.variant,
                    "episode_id": self.episode_id,
                    "collision": collision_value,
                    "success": self.success,
                    "timeout": timeout_value,
                    "distance_m": distance,
                    "route_length_m": self.route_length_m if self.route_length_m is not None else "",
                    "mean_speed_kmh": mean_speed,
                    "driving_score": driving_score,
                    "metric_source": metric_source,
                    "metric_note": ";".join(notes),
                }
            )
        self.get_logger().info(f"wrote episode metrics to {path}")

    def timeout_value(self):
        if self.success or self.collision:
            return False
        if self.assume_timeout_on_stop:
            return True
        return ""

    def distance_m(self):
        if self.first_pose is None or self.last_pose is None:
            return float("nan")
        return math.hypot(self.last_pose[0] - self.first_pose[0], self.last_pose[1] - self.first_pose[1])

    def is_uninitialized_pose(self, current):
        if self.goal_x is None or self.goal_y is None or self.route_length_m is None:
            return False
        distance_to_goal = math.hypot(current[0] - self.goal_x, current[1] - self.goal_y)
        threshold = max(1000.0, self.route_length_m * 5.0, self.goal_radius_m * 4.0)
        return distance_to_goal > threshold

    def driving_score(self, distance_m, mean_speed_kmh):
        progress = 0.0
        if self.route_length_m and not math.isnan(distance_m):
            progress = max(0.0, min(1.0, distance_m / self.route_length_m))
        speed_score = 0.0 if math.isnan(mean_speed_kmh) else max(0.0, min(1.0, mean_speed_kmh / 120.0))
        score = 100.0 * (0.55 * progress + 0.25 * speed_score + 0.20 * float(self.success))
        if self.collision:
            score *= 0.35
        return score


def optional_float(value):
    if value in ("", None):
        return None
    return float(value)


def env_bool(value, default):
    if value in ("", None):
        return default
    return str(value).lower() in {"1", "true", "yes", "on"}


def mean(values):
    if not values:
        return float("nan")
    return sum(values) / len(values)


def main():
    rclpy.init()
    node = EpisodeMetricMonitor()
    stop_requested = False

    def handle_signal(signum, frame):
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    try:
        while rclpy.ok() and not stop_requested:
            rclpy.spin_once(node, timeout_sec=0.2)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    except RCLError as exc:
        if not is_expected_shutdown_error(exc):
            raise
    finally:
        node.write_row()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
