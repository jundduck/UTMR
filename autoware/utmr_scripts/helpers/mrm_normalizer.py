#!/usr/bin/env python3
import rclpy
from rclpy._rclpy_pybind11 import RCLError
from autoware_adapi_v1_msgs.msg import MrmState
from helper_shutdown import is_expected_shutdown_error
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.parameter import Parameter


class MrmNormalizer(Node):
    def __init__(self):
        super().__init__("codex_mrm_normalizer")
        self.set_parameters([Parameter("use_sim_time", Parameter.Type.BOOL, True)])
        self.publisher = self.create_publisher(MrmState, "/system/fail_safe/mrm_state", 10)
        self.timer = self.create_timer(0.02, self.publish_normal)
        self.count = 0

    def publish_normal(self):
        msg = MrmState()
        msg.stamp = self.get_clock().now().to_msg()
        msg.state = MrmState.NORMAL
        msg.behavior = MrmState.NONE
        self.publisher.publish(msg)
        self.count += 1
        if self.count == 1 or self.count % 250 == 0:
            self.get_logger().info(f"published normal mrm #{self.count}")


def main():
    rclpy.init()
    node = MrmNormalizer()
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
