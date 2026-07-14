#!/usr/bin/env python3
import rclpy
from rclpy._rclpy_pybind11 import RCLError
from autoware_vehicle_msgs.msg import GearCommand
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.parameter import Parameter


class DriveGearInjector(Node):
    def __init__(self):
        super().__init__("codex_drive_gear_injector")
        self.set_parameters([Parameter("use_sim_time", Parameter.Type.BOOL, True)])
        self.publisher = self.create_publisher(GearCommand, "/control/shift_decider/gear_cmd", 10)
        self.timer = self.create_timer(0.05, self.publish_drive)
        self.count = 0

    def publish_drive(self):
        msg = GearCommand()
        msg.stamp = self.get_clock().now().to_msg()
        msg.command = GearCommand.DRIVE
        self.publisher.publish(msg)
        self.count += 1
        if self.count == 1 or self.count % 200 == 0:
            self.get_logger().info(f"published drive gear #{self.count}")


def main():
    rclpy.init()
    node = DriveGearInjector()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException, RCLError):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
