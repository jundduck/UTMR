#!/usr/bin/env python3
# allow: SIZE_OK - ROS executable helper owns subscriptions, UTMR scoring, and Autoware trajectory publishing.
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
import rclpy
from rclpy._rclpy_pybind11 import RCLError
from autoware_localization_msgs.msg import KinematicState
from autoware_perception_msgs.msg import DetectedObjects
from autoware_perception_msgs.msg import PredictedObjects
from autoware_perception_msgs.msg import TrackedObjects
from autoware_planning_msgs.msg import Trajectory
from autoware_planning_msgs.msg import TrajectoryPoint
from geometry_msgs.msg import Quaternion
from helper_shutdown import is_expected_shutdown_error
from nav_msgs.msg import Odometry
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.parameter import Parameter
from std_msgs.msg import String


UTMR_ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_DIR = UTMR_ROOT / "experiments" / "utmr"
sys.path.insert(0, str(EXPERIMENT_DIR))

from utmr_core import EgoState, Obstacle, UTMRPlanner, UTMRRuntimeConfig  # noqa: E402


def yaw_from_quat(q: Quaternion) -> float:
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


def quat_from_yaw(yaw: float) -> Quaternion:
    q = Quaternion()
    q.z = math.sin(yaw * 0.5)
    q.w = math.cos(yaw * 0.5)
    return q


def parse_obstacles(text: str) -> list[Obstacle]:
    if not text:
        return []
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("obstacles must be a JSON list")
    obstacles = []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"obstacle {index} must be an object")
        x_value = item.get("x_m", item.get("x"))
        y_value = item.get("y_m", item.get("y"))
        if x_value is None or y_value is None:
            raise ValueError(f"obstacle {index} is missing x/y")
        x_m = float(x_value)
        y_m = float(y_value)
        radius_m = float(item.get("radius_m", item.get("radius", 1.0)))
        if not all(math.isfinite(value) for value in (x_m, y_m, radius_m)):
            raise ValueError(f"obstacle {index} contains non-finite values")
        if abs(x_m) > 10_000_000.0 or abs(y_m) > 10_000_000.0:
            raise ValueError(f"obstacle {index} is outside supported map bounds")
        if radius_m <= 0.0 or radius_m > 1_000.0:
            raise ValueError(f"obstacle {index} radius is outside supported bounds")
        obstacles.append(Obstacle(x_m=x_m, y_m=y_m, radius_m=radius_m, label=str(item.get("label", "obstacle"))))
    return obstacles


def parse_route_points(text: str) -> list[tuple[float, float, float]]:
    if not text:
        return []
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("route points must be a JSON list")
    points = []
    for index, item in enumerate(data):
        if isinstance(item, dict):
            x_value = item.get("x", item.get("x_m"))
            y_value = item.get("y", item.get("y_m"))
            z_value = item.get("z", item.get("z_m", 0.0))
        elif isinstance(item, (list, tuple)):
            if len(item) < 2:
                raise ValueError(f"route point {index} is missing x/y")
            x_value = item[0]
            y_value = item[1]
            z_value = item[2] if len(item) > 2 else 0.0
        else:
            raise ValueError(f"route point {index} must be an object or list")
        if x_value is None or y_value is None:
            raise ValueError(f"route point {index} is missing x/y")
        point = (float(x_value), float(y_value), float(z_value))
        if not all(math.isfinite(value) for value in point):
            raise ValueError(f"route point {index} contains non-finite values")
        if abs(point[0]) > 10_000_000.0 or abs(point[1]) > 10_000_000.0 or abs(point[2]) > 10_000.0:
            raise ValueError(f"route point {index} is outside supported map bounds")
        points.append(point)
    return points


def finite_env(name: str, default: str, lower: float, upper: float) -> float:
    value = float(os.environ.get(name, default))
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    if value < lower or value > upper:
        raise ValueError(f"{name}={value} outside [{lower}, {upper}]")
    return value


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def object_radius(shape) -> float:
    points = getattr(getattr(shape, "footprint", None), "points", [])
    if points:
        return max(0.5, max(math.hypot(point.x, point.y) for point in points))
    dims = getattr(shape, "dimensions", None)
    if dims is None:
        return 1.0
    return max(0.5, 0.5 * max(abs(dims.x), abs(dims.y)))


def object_pose(obj):
    kinematics = obj.kinematics
    if hasattr(kinematics, "initial_pose_with_covariance"):
        return kinematics.initial_pose_with_covariance.pose
    return kinematics.pose_with_covariance.pose


class UTMRPlannerNode(Node):
    def __init__(self):
        super().__init__("utmr_planner_node")
        self.set_parameters([Parameter("use_sim_time", Parameter.Type.BOOL, True)])

        self.mode = os.environ.get("UTMR_MODE", "utmr")
        self.log_path = os.environ.get("UTMR_STEP_LOG", "")
        self.static_obstacles = parse_obstacles(os.environ.get("UTMR_OBSTACLES_JSON", ""))
        self.static_obstacle_frame = os.environ.get("UTMR_STATIC_OBSTACLE_FRAME", "ego")
        self.obstacle_topic = os.environ.get("UTMR_OBJECTS_TOPIC", "/perception/object_recognition/objects")
        self.obstacle_msg_type = os.environ.get("UTMR_OBJECTS_MSG_TYPE", "PredictedObjects")
        self.obstacle_min_probability = float(os.environ.get("UTMR_OBJECT_MIN_PROBABILITY", "0.1"))
        self.obstacle_max_range_m = float(os.environ.get("UTMR_OBJECT_MAX_RANGE_M", "120.0"))
        self.kinematic_topic = os.environ.get("UTMR_KINEMATIC_TOPIC", "/localization/kinematic_state")
        self.kinematic_msg_type = os.environ.get("UTMR_KINEMATIC_MSG_TYPE", "Odometry")
        self.fallback_pose = (
            float(os.environ.get("UTMR_FALLBACK_X", "81377.34")),
            float(os.environ.get("UTMR_FALLBACK_Y", "49916.89")),
            float(os.environ.get("UTMR_FALLBACK_Z", "41.30")),
            float(os.environ.get("UTMR_FALLBACK_YAW", "0.5895")),
        )
        self.fallback_speed_mps = float(os.environ.get("UTMR_FALLBACK_SPEED_MPS", "8.0"))
        self.publish_horizon_s = float(os.environ.get("UTMR_PUBLISH_HORIZON_S", "4.0"))
        self.publish_dt_s = float(os.environ.get("UTMR_PUBLISH_DT_S", "0.2"))
        self.route_guidance_enabled = os.environ.get("UTMR_ENABLE_ROUTE_GUIDANCE", "1") != "0"
        self.route_lookahead_m = finite_env("UTMR_ROUTE_LOOKAHEAD_M", "25.0", 1.0, 200.0)
        self.route_max_yaw_rad = finite_env("UTMR_ROUTE_MAX_YAW_RAD", "0.75", 0.0, 1.57)
        self.route_max_lateral_m = finite_env("UTMR_ROUTE_MAX_LATERAL_M", "20.0", 0.0, 50.0)
        self.route_points_map = self.load_route_points()

        self.config = UTMRRuntimeConfig(
            k=int(os.environ.get("UTMR_K", "64")),
            coarse_horizon_s=float(os.environ.get("UTMR_COARSE_HORIZON_S", "2.0")),
            coarse_dt_s=float(os.environ.get("UTMR_COARSE_DT_S", "0.20")),
            fine_horizon_s=float(os.environ.get("UTMR_FINE_HORIZON_S", "1.0")),
            fine_dt_s=float(os.environ.get("UTMR_FINE_DT_S", "0.05")),
            beta=float(os.environ.get("UTMR_BETA", "1.0")),
            gamma_h=float(os.environ.get("UTMR_GAMMA_H", "0.75")),
            gamma_m=float(os.environ.get("UTMR_GAMMA_M", "0.05")),
            top_n=int(os.environ.get("UTMR_TOP_N", "8")),
            ttc_threshold_s=float(os.environ.get("UTMR_TTC_THRESHOLD_S", "1.0")),
        )
        self.planner = UTMRPlanner(self.config)

        self.publisher = self.create_publisher(Trajectory, "/planning/trajectory", 10)
        self.create_kinematic_subscription()
        self.dynamic_obstacles_map: list[Obstacle] = []
        self.dynamic_obstacles_frame_id = "map"
        self.create_object_subscription()
        self.timer = self.create_timer(0.1, self.publish_trajectory)
        self.last_pose = None
        self.last_twist = None
        self.count = 0

    def create_kinematic_subscription(self):
        if self.kinematic_msg_type == "KinematicState":
            self.create_subscription(KinematicState, self.kinematic_topic, self.on_kinematic_state, 10)
        else:
            if self.kinematic_msg_type != "Odometry":
                self.get_logger().warning(f"unknown UTMR_KINEMATIC_MSG_TYPE={self.kinematic_msg_type}; using Odometry")
            self.create_subscription(Odometry, self.kinematic_topic, self.on_odometry, 10)
        self.get_logger().info(f"subscribed kinematic topic {self.kinematic_topic} as {self.kinematic_msg_type}")

    def create_object_subscription(self):
        msg_types = {
            "PredictedObjects": PredictedObjects,
            "TrackedObjects": TrackedObjects,
            "DetectedObjects": DetectedObjects,
        }
        msg_type = msg_types.get(self.obstacle_msg_type)
        if msg_type is None:
            self.get_logger().warning(f"unknown UTMR_OBJECTS_MSG_TYPE={self.obstacle_msg_type}; obstacle topic disabled")
            return
        self.create_subscription(msg_type, self.obstacle_topic, self.on_objects, 10)
        self.create_subscription(String, "/utmr/obstacles_json", self.on_obstacles_json, 10)
        self.get_logger().info(f"subscribed obstacle topic {self.obstacle_topic} as {self.obstacle_msg_type}")

    def on_kinematic_state(self, msg: KinematicState):
        self.last_pose = msg.pose_with_covariance.pose
        self.last_twist = msg.twist_with_covariance.twist

    def on_odometry(self, msg: Odometry):
        self.last_pose = msg.pose.pose
        self.last_twist = msg.twist.twist

    def on_objects(self, msg):
        obstacles = []
        for index, obj in enumerate(msg.objects):
            probability = float(getattr(obj, "existence_probability", 1.0))
            if not math.isfinite(probability):
                obstacles.append(Obstacle(0.0, 0.0, self.config.ego_radius_m, f"invalid_object_{index}"))
                continue
            if probability < self.obstacle_min_probability:
                continue
            pose = object_pose(obj)
            radius = object_radius(obj.shape)
            if not all(math.isfinite(value) for value in (pose.position.x, pose.position.y, radius)):
                obstacles.append(Obstacle(0.0, 0.0, self.config.ego_radius_m, f"invalid_object_{index}"))
                continue
            if radius <= 0.0 or radius > 1_000.0:
                obstacles.append(Obstacle(0.0, 0.0, self.config.ego_radius_m, f"invalid_object_{index}"))
                continue
            if math.hypot(pose.position.x, pose.position.y) > 1e6:
                continue
            obstacles.append(Obstacle(pose.position.x, pose.position.y, radius, f"object_{index}"))
        self.dynamic_obstacles_map = obstacles
        self.dynamic_obstacles_frame_id = getattr(msg.header, "frame_id", "map") or "map"

    def on_obstacles_json(self, msg: String):
        self.static_obstacles = parse_obstacles(msg.data)

    def current_state(self):
        if self.last_pose is None or self.last_twist is None:
            x, y, z, yaw = self.fallback_pose
            return x, y, z, yaw, self.fallback_speed_mps

        pose = self.last_pose
        twist = self.last_twist
        speed = math.hypot(twist.linear.x, twist.linear.y)
        return (
            pose.position.x,
            pose.position.y,
            pose.position.z,
            yaw_from_quat(pose.orientation),
            speed,
        )

    def obstacle_to_local(self, obstacle: Obstacle, frame_id: str, x: float, y: float, yaw: float) -> Obstacle:
        frame = frame_id.strip("/")
        if frame in {"base_link", "base_footprint", "base_link_center", "ego", ""}:
            return obstacle
        cos_yaw = math.cos(-yaw)
        sin_yaw = math.sin(-yaw)
        dx = obstacle.x_m - x
        dy = obstacle.y_m - y
        local_x = cos_yaw * dx - sin_yaw * dy
        local_y = sin_yaw * dx + cos_yaw * dy
        return Obstacle(local_x, local_y, obstacle.radius_m, obstacle.label)

    def obstacles_for_planning(self, x: float, y: float, yaw: float) -> list[Obstacle]:
        obstacles = []
        for obstacle in self.static_obstacles:
            local = self.obstacle_to_local(obstacle, self.static_obstacle_frame, x, y, yaw)
            if -10.0 <= local.x_m <= self.obstacle_max_range_m:
                obstacles.append(local)
        frame_id = self.dynamic_obstacles_frame_id.strip("/")
        for obstacle in self.dynamic_obstacles_map:
            local = self.obstacle_to_local(obstacle, frame_id, x, y, yaw)
            if -10.0 <= local.x_m <= self.obstacle_max_range_m:
                obstacles.append(local)
        return obstacles

    def point_to_local(self, point: tuple[float, float, float], x: float, y: float, yaw: float) -> tuple[float, float]:
        cos_yaw = math.cos(-yaw)
        sin_yaw = math.sin(-yaw)
        dx = point[0] - x
        dy = point[1] - y
        return cos_yaw * dx - sin_yaw * dy, sin_yaw * dx + cos_yaw * dy

    def route_target_local(self, x: float, y: float, yaw: float) -> tuple[float, float] | None:
        if not self.route_guidance_enabled or not self.route_points_map:
            return None
        local_points = [
            self.point_to_local(point, x, y, yaw)
            for point in self.route_points_map
        ]
        ahead_points = [point for point in local_points if point[0] > 1.0]
        if not ahead_points:
            return None
        polyline = [(0.0, 0.0)] + ahead_points
        remaining = max(1.0, self.route_lookahead_m)
        for start, end in zip(polyline, polyline[1:]):
            dx = end[0] - start[0]
            dy = end[1] - start[1]
            length = math.hypot(dx, dy)
            if length < 1e-6:
                continue
            if remaining <= length:
                ratio = remaining / length
                return start[0] + dx * ratio, start[1] + dy * ratio
            remaining -= length
        return polyline[-1]

    def apply_route_guidance(
        self,
        selected: np.ndarray,
        route_target: tuple[float, float] | None,
    ) -> tuple[np.ndarray, bool]:
        if route_target is None:
            return selected, False
        target_x = max(2.0, float(route_target[0]))
        target_y = clamp(float(route_target[1]), -self.route_max_lateral_m, self.route_max_lateral_m)
        target_yaw = clamp(math.atan2(target_y, target_x), -self.route_max_yaw_rad, self.route_max_yaw_rad)
        if abs(target_y) < 0.25 and abs(target_yaw) < 0.03:
            return selected, False

        guided = selected.copy()
        end_slope = math.tan(target_yaw)
        a = (target_x * end_slope - 2.0 * target_y) / (target_x ** 3)
        b = (3.0 * target_y - target_x * end_slope) / (target_x ** 2)
        for idx in range(guided.shape[0]):
            local_x = max(0.0, float(guided[idx, 0]))
            fit_x = min(local_x, target_x)
            route_y = a * fit_x ** 3 + b * fit_x ** 2
            route_slope = 3.0 * a * fit_x ** 2 + 2.0 * b * fit_x
            if local_x > target_x:
                route_y = target_y
                route_slope = 0.0
            guided[idx, 1] = float(route_y + guided[idx, 1])
            guided[idx, 2] = float(math.atan(route_slope) + guided[idx, 2])
        return guided, True

    def trajectory_safety_reject_reason(self, trajectory: np.ndarray, obstacles: list[Obstacle]) -> str:
        if not np.all(np.isfinite(trajectory)):
            return "non_finite"
        max_abs_y = float(np.max(np.abs(trajectory[:, 1]))) if trajectory.size else 0.0
        if max_abs_y > self.config.lane_half_width_m:
            return f"drivability:lateral={max_abs_y:.2f}"
        if not obstacles:
            return ""
        xy = trajectory[:, :2].astype(np.float64)
        for obstacle in obstacles:
            if not all(math.isfinite(value) for value in (obstacle.x_m, obstacle.y_m, obstacle.radius_m)):
                return f"obstacle_non_finite:{obstacle.label}"
            if obstacle.radius_m <= 0.0 or obstacle.radius_m > 1_000.0:
                return f"obstacle_radius:{obstacle.label}"
            center = np.asarray([obstacle.x_m, obstacle.y_m], dtype=np.float64)
            threshold = self.config.ego_radius_m + obstacle.radius_m
            if bool(np.any(np.linalg.norm(xy - center[None, :], axis=1) <= threshold)):
                return f"collision:{obstacle.label}"
        return ""

    def publish_trajectory(self):
        start_time = time.perf_counter()
        x, y, z, yaw, speed = self.current_state()
        obstacles = self.obstacles_for_planning(x, y, yaw)
        result = self.planner.plan(EgoState(speed_mps=max(speed, 0.1)), mode=self.mode, obstacles=obstacles)
        latency_ms = (time.perf_counter() - start_time) * 1000.0

        msg = Trajectory()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "map"

        selected = result.candidates.sample(self.publish_horizon_s, self.publish_dt_s)[result.selected_index]
        route_target = self.route_target_local(x, y, yaw)
        guided_selected, route_guided = self.apply_route_guidance(selected, route_target)
        route_guidance_reject_reason = ""
        if route_guided:
            route_guidance_reject_reason = self.trajectory_safety_reject_reason(guided_selected, obstacles)
            if route_guidance_reject_reason:
                route_guided = False
                self.get_logger().warning(
                    f"rejected route-guided trajectory reason={route_guidance_reject_reason}; "
                    "publishing original UTMR selection"
                )
            else:
                selected = guided_selected
        cos_yaw = math.cos(yaw)
        sin_yaw = math.sin(yaw)
        for idx, pose in enumerate(selected):
            local_x = float(pose[0])
            local_y = float(pose[1])
            local_yaw = float(pose[2])

            point = TrajectoryPoint()
            elapsed = (idx + 1) * self.publish_dt_s
            point.time_from_start.sec = int(elapsed)
            point.time_from_start.nanosec = int((elapsed - int(elapsed)) * 1e9)
            point.pose.position.x = x + cos_yaw * local_x - sin_yaw * local_y
            point.pose.position.y = y + sin_yaw * local_x + cos_yaw * local_y
            point.pose.position.z = z
            point.pose.orientation = quat_from_yaw(yaw + local_yaw)
            point.longitudinal_velocity_mps = max(0.0, float(result.candidates.target_speeds_mps[result.selected_index]))
            point.lateral_velocity_mps = 0.0
            point.acceleration_mps2 = 0.0
            point.heading_rate_rps = 0.0
            point.front_wheel_angle_rad = 0.0
            point.rear_wheel_angle_rad = 0.0
            msg.points.append(point)

        self.publisher.publish(msg)
        self.write_step_log(result, speed, latency_ms, route_target, route_guided, route_guidance_reject_reason)

        self.count += 1
        if self.count == 1 or self.count % 50 == 0:
            route_text = ""
            if route_target is not None:
                route_text = f" route_target=({route_target[0]:.1f},{route_target[1]:.1f}) guided={route_guided}"
            self.get_logger().info(
                f"published UTMR trajectory #{self.count} mode={self.mode} "
                f"selected={result.selected_index} triggered={result.triggered}{route_text}"
            )

    def load_route_points(self) -> list[tuple[float, float, float]]:
        route_points = parse_route_points(
            os.environ.get("UTMR_ROUTE_POINTS_JSON", os.environ.get("UTMR_ROUTE_WAYPOINTS_JSON", ""))
        )
        goal_x = os.environ.get("UTMR_GOAL_X")
        goal_y = os.environ.get("UTMR_GOAL_Y")
        if goal_x is not None and goal_y is not None:
            goal_z = os.environ.get("UTMR_GOAL_Z", "0.0")
            route_points.extend(parse_route_points(json.dumps([{"x": goal_x, "y": goal_y, "z": goal_z}])))
        return route_points

    def write_step_log(
        self,
        result,
        speed_mps: float,
        latency_ms: float,
        route_target: tuple[float, float] | None,
        route_guided: bool,
        route_guidance_reject_reason: str,
    ):
        if not self.log_path:
            return
        row = result.to_step_log(
            episode_id=os.environ.get("UTMR_EPISODE_ID", "awsim_live"),
            step=self.count,
            latency_ms=latency_ms,
        )
        row["method_variant"] = self.mode
        row["ego_speed_kmh"] = float(speed_mps * 3.6)
        row["route_guided"] = bool(route_guided)
        row["route_guidance_reject_reason"] = route_guidance_reject_reason
        if route_target is not None:
            row["route_target_x_m"] = float(route_target[0])
            row["route_target_y_m"] = float(route_target[1])
        path = Path(self.log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(row, separators=(",", ":")) + "\n")


def main():
    rclpy.init()
    node = UTMRPlannerNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    except RCLError as exc:
        if not is_expected_shutdown_error(exc):
            raise
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
