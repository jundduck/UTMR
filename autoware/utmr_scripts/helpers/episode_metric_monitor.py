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
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.parameter import Parameter
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
        self.route_state_topic = os.environ.get("UTMR_ROUTE_STATE_TOPIC", "/api/routing/state")

        self.started_wall = time.monotonic()
        self.first_pose = None
        self.last_pose = None
        self.speed_samples_kmh = []
        self.collision = False
        self.success = False
        self.row_written = False

        self.create_subscription(KinematicState, "/localization/kinematic_state", self.on_kinematic_state, 10)
        self.create_subscription(RouteState, self.route_state_topic, self.on_route_state, 10)
        if self.collision_topic:
            self.create_subscription(Bool, self.collision_topic, self.on_collision, 10)
            self.get_logger().info(f"subscribed collision topic {self.collision_topic}")
        self.get_logger().info(f"metric monitor episode={self.episode_id} csv={self.episode_csv or '(disabled)'}")

    def on_kinematic_state(self, msg: KinematicState):
        pose = msg.pose_with_covariance.pose
        twist = msg.twist_with_covariance.twist
        current = (pose.position.x, pose.position.y)
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
                    "collision": self.collision,
                    "success": self.success,
                    "timeout": not self.collision and not self.success,
                    "distance_m": distance,
                    "route_length_m": self.route_length_m if self.route_length_m is not None else "",
                    "mean_speed_kmh": mean_speed,
                    "driving_score": driving_score,
                    "metric_source": "observed",
                    "metric_note": "",
                }
            )
        self.get_logger().info(f"wrote episode metrics to {path}")

    def distance_m(self):
        if self.first_pose is None or self.last_pose is None:
            return float("nan")
        return math.hypot(self.last_pose[0] - self.first_pose[0], self.last_pose[1] - self.first_pose[1])

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


def mean(values):
    if not values:
        return float("nan")
    return sum(values) / len(values)


def main():
    rclpy.init()
    node = EpisodeMetricMonitor()

    def handle_signal(signum, frame):
        node.write_row()
        if rclpy.ok():
            rclpy.shutdown()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    try:
        rclpy.spin(node)
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
