#!/usr/bin/env python3
import math
import os
import time

import rclpy
from autoware_adapi_v1_msgs.msg import VehicleKinematics
from autoware_vehicle_msgs.msg import VelocityReport
from geometry_msgs.msg import TwistWithCovarianceStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.parameter import Parameter


class StationaryWaiter(Node):
    def __init__(self) -> None:
        super().__init__("utmr_wait_for_stationary")
        self.set_parameters([Parameter("use_sim_time", Parameter.Type.BOOL, True)])
        self.topic = os.environ.get(
            "UTMR_STATIONARY_TOPIC", "/sensing/vehicle_velocity_converter/twist_with_covariance"
        )
        self.msg_type = os.environ.get("UTMR_STATIONARY_MSG_TYPE", "TwistWithCovarianceStamped")
        self.speed_threshold_mps = float(os.environ.get("UTMR_STATIONARY_SPEED_MPS", "0.001"))
        self.hold_s = float(os.environ.get("UTMR_STATIONARY_HOLD_S", "3.0"))
        self.latest_speed_mps: float | None = None
        self.below_since: float | None = None
        self.stationary = False
        self.create_speed_subscription()
        self.get_logger().info(
            f"waiting for stationary on {self.topic} as {self.msg_type}: "
            f"speed<={self.speed_threshold_mps:.3f}m/s hold={self.hold_s:.1f}s"
        )

    def create_speed_subscription(self) -> None:
        if self.msg_type == "VehicleKinematics":
            self.create_subscription(VehicleKinematics, self.topic, self.on_vehicle_kinematics, 10)
        elif self.msg_type == "VelocityReport":
            self.create_subscription(VelocityReport, self.topic, self.on_velocity_report, 10)
        elif self.msg_type == "TwistWithCovarianceStamped":
            self.create_subscription(TwistWithCovarianceStamped, self.topic, self.on_twist_with_covariance, 10)
        else:
            if self.msg_type != "Odometry":
                self.get_logger().warning(f"unknown UTMR_STATIONARY_MSG_TYPE={self.msg_type}; using Odometry")
            self.create_subscription(Odometry, self.topic, self.on_odometry, 10)

    def on_odometry(self, msg: Odometry) -> None:
        twist = msg.twist.twist
        self.record_speed(math.hypot(twist.linear.x, twist.linear.y))

    def on_vehicle_kinematics(self, msg: VehicleKinematics) -> None:
        twist = msg.twist.twist
        self.record_speed(math.sqrt(twist.linear.x**2 + twist.linear.y**2 + twist.linear.z**2))

    def on_twist_with_covariance(self, msg: TwistWithCovarianceStamped) -> None:
        twist = msg.twist.twist
        self.record_speed(math.sqrt(twist.linear.x**2 + twist.linear.y**2 + twist.linear.z**2))

    def on_velocity_report(self, msg: VelocityReport) -> None:
        self.record_speed(math.hypot(msg.longitudinal_velocity, msg.lateral_velocity))

    def record_speed(self, speed_mps: float) -> None:
        now = time.monotonic()
        self.latest_speed_mps = speed_mps
        if speed_mps <= self.speed_threshold_mps:
            if self.below_since is None:
                self.below_since = now
            self.stationary = now - self.below_since >= self.hold_s
        else:
            self.below_since = None
            self.stationary = False


def main() -> int:
    timeout_s = float(os.environ.get("UTMR_STATIONARY_TIMEOUT_S", "45.0"))
    rclpy.init()
    node = StationaryWaiter()
    deadline = time.monotonic() + timeout_s
    try:
        while rclpy.ok() and time.monotonic() < deadline and not node.stationary:
            rclpy.spin_once(node, timeout_sec=0.1)
        if node.stationary:
            node.get_logger().info(f"stationary confirmed speed_mps={node.latest_speed_mps:.3f}")
            return 0
        latest = "none" if node.latest_speed_mps is None else f"{node.latest_speed_mps:.3f}"
        node.get_logger().warning(f"stationary wait timed out latest_speed_mps={latest}")
        return 1
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
