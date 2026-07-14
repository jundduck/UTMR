#!/usr/bin/env python3
import os
import uuid

import rclpy
from rclpy._rclpy_pybind11 import RCLError
from autoware_planning_msgs.msg import LaneletRoute
from geometry_msgs.msg import Pose
from helper_shutdown import is_expected_shutdown_error
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import DurabilityPolicy
from rclpy.qos import HistoryPolicy
from rclpy.qos import QoSProfile
from rclpy.qos import ReliabilityPolicy


def pose_from_env(prefix: str) -> Pose:
    pose = Pose()
    pose.position.x = float(os.environ.get(f"{prefix}_X", "0.0"))
    pose.position.y = float(os.environ.get(f"{prefix}_Y", "0.0"))
    pose.position.z = float(os.environ.get(f"{prefix}_Z", "0.0"))
    pose.orientation.x = float(os.environ.get(f"{prefix}_QX", "0.0"))
    pose.orientation.y = float(os.environ.get(f"{prefix}_QY", "0.0"))
    pose.orientation.z = float(os.environ.get(f"{prefix}_QZ", "0.0"))
    pose.orientation.w = float(os.environ.get(f"{prefix}_QW", "1.0"))
    return pose


class RoutePublisher(Node):
    def __init__(self):
        super().__init__("utmr_route_publisher")
        self.set_parameters([Parameter("use_sim_time", Parameter.Type.BOOL, True)])
        qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self.publisher = self.create_publisher(LaneletRoute, "/planning/mission_planning/route", qos)
        self.route_uuid = uuid.UUID(os.environ.get("UTMR_ROUTE_UUID", "11111111-2222-3333-4444-555555555555"))
        self.timer = self.create_timer(0.5, self.publish_route)
        self.count = 0

    def publish_route(self):
        msg = LaneletRoute()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "map"
        msg.start_pose = pose_from_env("UTMR_INIT")
        msg.goal_pose = pose_from_env("UTMR_GOAL")
        msg.uuid.uuid = list(self.route_uuid.bytes)
        msg.allow_modification = True
        self.publisher.publish(msg)
        self.count += 1
        if self.count == 1 or self.count % 20 == 0:
            self.get_logger().info(f"published synthetic mission route #{self.count}")


def main():
    rclpy.init()
    node = RoutePublisher()
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
