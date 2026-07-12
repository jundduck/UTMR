#!/usr/bin/env python3
import math

import rclpy
from autoware_planning_msgs.msg import Trajectory
from autoware_planning_msgs.msg import TrajectoryPoint
from geometry_msgs.msg import Quaternion
from rclpy.node import Node
from rclpy.parameter import Parameter


def slerp_yaw(q: Quaternion) -> float:
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


def quat_from_yaw(yaw: float) -> Quaternion:
    q = Quaternion()
    q.z = math.sin(yaw * 0.5)
    q.w = math.cos(yaw * 0.5)
    return q


class StraightTrajectoryPublisher(Node):
    def __init__(self):
        super().__init__("codex_straight_trajectory_publisher")
        self.set_parameters([Parameter("use_sim_time", Parameter.Type.BOOL, True)])
        self.publisher = self.create_publisher(Trajectory, "/planning/trajectory", 10)
        self.start = (81377.34, 49916.89, 41.30)
        self.goal = (81393.98, 49928.02, 41.32)
        self.yaw = 0.5895
        self.timer = self.create_timer(0.1, self.publish_trajectory)
        self.count = 0

    def publish_trajectory(self):
        msg = Trajectory()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "map"

        n = 30
        for i in range(n):
            ratio = i / (n - 1)
            p = TrajectoryPoint()
            p.time_from_start.sec = int(i * 0.3)
            p.time_from_start.nanosec = int((i * 0.3 - int(i * 0.3)) * 1e9)
            p.pose.position.x = self.start[0] + (self.goal[0] - self.start[0]) * ratio
            p.pose.position.y = self.start[1] + (self.goal[1] - self.start[1]) * ratio
            p.pose.position.z = self.start[2] + (self.goal[2] - self.start[2]) * ratio
            p.pose.orientation = quat_from_yaw(self.yaw)
            p.longitudinal_velocity_mps = 1.5 if i < n - 4 else 0.0
            p.lateral_velocity_mps = 0.0
            p.acceleration_mps2 = 0.0
            p.heading_rate_rps = 0.0
            p.front_wheel_angle_rad = 0.0
            p.rear_wheel_angle_rad = 0.0
            msg.points.append(p)

        self.publisher.publish(msg)
        self.count += 1
        if self.count == 1 or self.count % 50 == 0:
            self.get_logger().info(f"published straight trajectory #{self.count}")


def main():
    rclpy.init()
    node = StraightTrajectoryPublisher()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
