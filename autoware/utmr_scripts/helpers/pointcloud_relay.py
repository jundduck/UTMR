#!/usr/bin/env python3
import rclpy
from rclpy._rclpy_pybind11 import RCLError
from rclpy.executors import ExternalShutdownException
from helper_shutdown import is_expected_shutdown_error
from rclpy.node import Node
from rclpy.qos import QoSHistoryPolicy
from rclpy.qos import QoSProfile
from rclpy.qos import QoSReliabilityPolicy
from sensor_msgs.msg import PointCloud2


class PointCloudRelay(Node):
    def __init__(self):
        super().__init__("codex_ndt_pointcloud_relay_py")
        qos = QoSProfile(
            depth=10,
            history=QoSHistoryPolicy.KEEP_LAST,
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
        )
        self.publisher = self.create_publisher(
            PointCloud2, "/localization/util/downsample/pointcloud", qos
        )
        self.subscription = self.create_subscription(
            PointCloud2, "/sensing/lidar/top/pointcloud_raw", self.relay, qos
        )
        self.count = 0

    def relay(self, msg):
        self.publisher.publish(msg)
        self.count += 1
        if self.count == 1 or self.count % 50 == 0:
            self.get_logger().info(
                f"relayed pointcloud #{self.count} stamp={msg.header.stamp.sec}.{msg.header.stamp.nanosec:09d}"
            )


def main():
    rclpy.init()
    node = PointCloudRelay()
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
