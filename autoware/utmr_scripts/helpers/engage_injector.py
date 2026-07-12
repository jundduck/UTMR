#!/usr/bin/env python3
import rclpy
from autoware_vehicle_msgs.msg import Engage
from rclpy.node import Node
from rclpy.parameter import Parameter


class EngageInjector(Node):
    def __init__(self):
        super().__init__("codex_engage_injector")
        self.set_parameters([Parameter("use_sim_time", Parameter.Type.BOOL, True)])
        self.publisher = self.create_publisher(Engage, "/autoware/engage", 10)
        self.timer = self.create_timer(0.05, self.publish_engage)
        self.count = 0

    def publish_engage(self):
        msg = Engage()
        msg.stamp = self.get_clock().now().to_msg()
        msg.engage = True
        self.publisher.publish(msg)
        self.count += 1
        if self.count == 1 or self.count % 200 == 0:
            self.get_logger().info(f"published engage #{self.count}")


def main():
    rclpy.init()
    node = EngageInjector()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
