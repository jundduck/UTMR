#!/usr/bin/env python3
import json
import math
import os
import sys
import time
from pathlib import Path

import rclpy
from autoware_localization_msgs.msg import KinematicState
from autoware_perception_msgs.msg import DetectedObjects
from autoware_perception_msgs.msg import PredictedObjects
from autoware_perception_msgs.msg import TrackedObjects
from autoware_planning_msgs.msg import Trajectory
from autoware_planning_msgs.msg import TrajectoryPoint
from geometry_msgs.msg import Quaternion
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
    return [
        Obstacle(
            x_m=float(item["x_m"]),
            y_m=float(item["y_m"]),
            radius_m=float(item.get("radius_m", 1.0)),
            label=str(item.get("label", "obstacle")),
        )
        for item in data
    ]


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
        self.fallback_pose = (
            float(os.environ.get("UTMR_FALLBACK_X", "81377.34")),
            float(os.environ.get("UTMR_FALLBACK_Y", "49916.89")),
            float(os.environ.get("UTMR_FALLBACK_Z", "41.30")),
            float(os.environ.get("UTMR_FALLBACK_YAW", "0.5895")),
        )
        self.fallback_speed_mps = float(os.environ.get("UTMR_FALLBACK_SPEED_MPS", "8.0"))
        self.publish_horizon_s = float(os.environ.get("UTMR_PUBLISH_HORIZON_S", "4.0"))
        self.publish_dt_s = float(os.environ.get("UTMR_PUBLISH_DT_S", "0.2"))

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
        self.create_subscription(KinematicState, "/localization/kinematic_state", self.on_kinematic_state, 10)
        self.dynamic_obstacles_map: list[Obstacle] = []
        self.dynamic_obstacles_frame_id = "map"
        self.create_object_subscription()
        self.timer = self.create_timer(0.1, self.publish_trajectory)
        self.last_state = None
        self.count = 0

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
        self.last_state = msg

    def on_objects(self, msg):
        obstacles = []
        for index, obj in enumerate(msg.objects):
            if getattr(obj, "existence_probability", 1.0) < self.obstacle_min_probability:
                continue
            pose = object_pose(obj)
            radius = object_radius(obj.shape)
            if math.hypot(pose.position.x, pose.position.y) > 1e6:
                continue
            obstacles.append(Obstacle(pose.position.x, pose.position.y, radius, f"object_{index}"))
        self.dynamic_obstacles_map = obstacles
        self.dynamic_obstacles_frame_id = getattr(msg.header, "frame_id", "map") or "map"

    def on_obstacles_json(self, msg: String):
        self.static_obstacles = parse_obstacles(msg.data)

    def current_state(self):
        if self.last_state is None:
            x, y, z, yaw = self.fallback_pose
            return x, y, z, yaw, self.fallback_speed_mps

        pose = self.last_state.pose_with_covariance.pose
        twist = self.last_state.twist_with_covariance.twist
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
        self.write_step_log(result, speed, latency_ms)

        self.count += 1
        if self.count == 1 or self.count % 50 == 0:
            self.get_logger().info(
                f"published UTMR trajectory #{self.count} mode={self.mode} "
                f"selected={result.selected_index} triggered={result.triggered}"
            )

    def write_step_log(self, result, speed_mps: float, latency_ms: float):
        if not self.log_path:
            return
        row = result.to_step_log(
            episode_id=os.environ.get("UTMR_EPISODE_ID", "awsim_live"),
            step=self.count,
            latency_ms=latency_ms,
        )
        row["method_variant"] = self.mode
        row["ego_speed_kmh"] = float(speed_mps * 3.6)
        path = Path(self.log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(row, separators=(",", ":")) + "\n")


def main():
    rclpy.init()
    node = UTMRPlannerNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
