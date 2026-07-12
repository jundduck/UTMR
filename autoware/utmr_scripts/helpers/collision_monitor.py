#!/usr/bin/env python3
import json
import math
import os

import rclpy
from autoware_localization_msgs.msg import KinematicState
from autoware_perception_msgs.msg import DetectedObjects
from autoware_perception_msgs.msg import PredictedObjects
from autoware_perception_msgs.msg import TrackedObjects
from rclpy.node import Node
from rclpy.parameter import Parameter
from std_msgs.msg import Bool


def parse_obstacles(text: str):
    if not text:
        return []
    data = json.loads(text)
    return [
        (
            float(item["x_m"]),
            float(item["y_m"]),
            float(item.get("radius_m", 1.0)),
        )
        for item in data
    ]


def yaw_from_quat(q) -> float:
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


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


class CollisionMonitor(Node):
    def __init__(self):
        super().__init__("utmr_collision_monitor")
        self.set_parameters([Parameter("use_sim_time", Parameter.Type.BOOL, True)])

        self.object_topic = os.environ.get("UTMR_OBJECTS_TOPIC", "/perception/object_recognition/objects")
        self.object_msg_type = os.environ.get("UTMR_OBJECTS_MSG_TYPE", "PredictedObjects")
        self.output_topic = os.environ.get("UTMR_COLLISION_OUTPUT_TOPIC", "/utmr/collision")
        self.static_obstacles = parse_obstacles(os.environ.get("UTMR_OBSTACLES_JSON", ""))
        self.static_obstacle_frame = os.environ.get("UTMR_STATIC_OBSTACLE_FRAME", "ego")
        self.ego_radius_m = float(os.environ.get("UTMR_COLLISION_EGO_RADIUS_M", "1.4"))
        self.margin_m = float(os.environ.get("UTMR_COLLISION_MARGIN_M", "0.2"))
        self.min_probability = float(os.environ.get("UTMR_OBJECT_MIN_PROBABILITY", "0.1"))

        self.last_pose = None
        self.objects = []
        self.objects_frame = "map"
        self.collision = False

        self.publisher = self.create_publisher(Bool, self.output_topic, 10)
        self.create_subscription(KinematicState, "/localization/kinematic_state", self.on_kinematic_state, 10)
        self.create_object_subscription()
        self.create_timer(0.1, self.publish_state)
        self.get_logger().info(f"collision monitor publishing {self.output_topic}")

    def create_object_subscription(self):
        msg_types = {
            "PredictedObjects": PredictedObjects,
            "TrackedObjects": TrackedObjects,
            "DetectedObjects": DetectedObjects,
        }
        msg_type = msg_types.get(self.object_msg_type)
        if msg_type is None:
            self.get_logger().warning(f"unknown UTMR_OBJECTS_MSG_TYPE={self.object_msg_type}; object input disabled")
            return
        self.create_subscription(msg_type, self.object_topic, self.on_objects, 10)
        self.get_logger().info(f"subscribed object topic {self.object_topic} as {self.object_msg_type}")

    def on_kinematic_state(self, msg: KinematicState):
        pose = msg.pose_with_covariance.pose
        self.last_pose = (
            pose.position.x,
            pose.position.y,
            yaw_from_quat(pose.orientation),
        )
        self.update_collision()

    def on_objects(self, msg):
        objects = []
        for obj in msg.objects:
            if getattr(obj, "existence_probability", 1.0) < self.min_probability:
                continue
            pose = object_pose(obj)
            objects.append((pose.position.x, pose.position.y, object_radius(obj.shape)))
        self.objects = objects
        self.objects_frame = getattr(msg.header, "frame_id", "map") or "map"
        self.update_collision()

    def to_local(self, x_m: float, y_m: float, frame_id: str):
        frame = frame_id.strip("/")
        if frame in {"base_link", "base_footprint", "base_link_center", "ego", ""}:
            return x_m, y_m
        if self.last_pose is None:
            return None
        ego_x, ego_y, ego_yaw = self.last_pose
        dx = x_m - ego_x
        dy = y_m - ego_y
        cos_yaw = math.cos(-ego_yaw)
        sin_yaw = math.sin(-ego_yaw)
        return cos_yaw * dx - sin_yaw * dy, sin_yaw * dx + cos_yaw * dy

    def update_collision(self):
        threshold_extra = self.ego_radius_m + self.margin_m
        for obs_x, obs_y, radius in self.static_obstacles:
            local = self.to_local(obs_x, obs_y, self.static_obstacle_frame)
            if local and math.hypot(local[0], local[1]) <= threshold_extra + radius:
                self.collision = True
        for obs_x, obs_y, radius in self.objects:
            local = self.to_local(obs_x, obs_y, self.objects_frame)
            if local and math.hypot(local[0], local[1]) <= threshold_extra + radius:
                self.collision = True

    def publish_state(self):
        msg = Bool()
        msg.data = self.collision
        self.publisher.publish(msg)


def main():
    rclpy.init()
    node = CollisionMonitor()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
