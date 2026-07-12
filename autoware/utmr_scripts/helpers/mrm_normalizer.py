#!/usr/bin/env python3
import rclpy
from autoware_adapi_v1_msgs.msg import MrmState
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
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
