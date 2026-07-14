#!/usr/bin/env python3
import rclpy
from rclpy._rclpy_pybind11 import RCLError
from autoware_adapi_v1_msgs.msg import ManualOperatorHeartbeat
from autoware_control_msgs.msg import Control
from autoware_vehicle_msgs.msg import GearCommand
from autoware_vehicle_msgs.msg import HazardLightsCommand
from autoware_vehicle_msgs.msg import TurnIndicatorsCommand
from helper_shutdown import is_expected_shutdown_error
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.parameter import Parameter
from tier4_control_msgs.msg import GateMode


class DriveGearInjector(Node):
    def __init__(self):
        super().__init__("codex_drive_gear_injector")
        self.set_parameters([Parameter("use_sim_time", Parameter.Type.BOOL, True)])
        self.gear_publishers = [
            self.create_publisher(GearCommand, "/control/shift_decider/gear_cmd", 10),
            self.create_publisher(GearCommand, "/external/selected/gear_cmd", 10),
            self.create_publisher(GearCommand, "/system/emergency/gear_cmd", 10),
        ]
        self.turn_publishers = [
            self.create_publisher(TurnIndicatorsCommand, "/planning/turn_indicators_cmd", 10),
            self.create_publisher(TurnIndicatorsCommand, "/external/selected/turn_indicators_cmd", 10),
            self.create_publisher(TurnIndicatorsCommand, "/system/emergency/turn_indicators_cmd", 10),
        ]
        self.hazard_publishers = [
            self.create_publisher(HazardLightsCommand, "/planning/hazard_lights_cmd", 10),
            self.create_publisher(HazardLightsCommand, "/planning/behavior_path_planner/hazard_lights_cmd", 10),
            self.create_publisher(HazardLightsCommand, "/external/selected/hazard_lights_cmd", 10),
            self.create_publisher(HazardLightsCommand, "/system/emergency/hazard_lights_cmd", 10),
        ]
        self.control_publishers = [
            self.create_publisher(Control, "/external/selected/control_cmd", 10),
            self.create_publisher(Control, "/system/emergency/control_cmd", 10),
        ]
        self.gate_mode_publisher = self.create_publisher(GateMode, "/control/gate_mode_cmd", 10)
        self.heartbeat_publisher = self.create_publisher(
            ManualOperatorHeartbeat, "/external/selected/heartbeat", 10
        )
        self.timer = self.create_timer(0.05, self.publish_commands)
        self.count = 0

    def publish_commands(self):
        stamp = self.get_clock().now().to_msg()

        gear = GearCommand()
        gear.stamp = stamp
        gear.command = GearCommand.DRIVE
        for publisher in self.gear_publishers:
            publisher.publish(gear)

        turn = TurnIndicatorsCommand()
        turn.stamp = stamp
        turn.command = TurnIndicatorsCommand.DISABLE
        for publisher in self.turn_publishers:
            publisher.publish(turn)

        hazard = HazardLightsCommand()
        hazard.stamp = stamp
        hazard.command = HazardLightsCommand.DISABLE
        for publisher in self.hazard_publishers:
            publisher.publish(hazard)

        control = Control()
        control.stamp = stamp
        control.control_time = stamp
        control.lateral.stamp = stamp
        control.lateral.control_time = stamp
        control.lateral.steering_tire_angle = 0.0
        control.lateral.steering_tire_rotation_rate = 0.0
        control.lateral.is_defined_steering_tire_rotation_rate = True
        control.longitudinal.stamp = stamp
        control.longitudinal.control_time = stamp
        control.longitudinal.velocity = 0.0
        control.longitudinal.acceleration = 0.0
        control.longitudinal.jerk = 0.0
        control.longitudinal.is_defined_acceleration = True
        control.longitudinal.is_defined_jerk = True
        for publisher in self.control_publishers:
            publisher.publish(control)

        gate_mode = GateMode()
        gate_mode.data = GateMode.AUTO
        self.gate_mode_publisher.publish(gate_mode)

        heartbeat = ManualOperatorHeartbeat()
        heartbeat.stamp = stamp
        heartbeat.ready = True
        self.heartbeat_publisher.publish(heartbeat)

        self.count += 1
        if self.count == 1 or self.count % 200 == 0:
            self.get_logger().info(
                f"published drive gear, turn, hazard, control, gate, heartbeat commands #{self.count}"
            )


def main():
    rclpy.init()
    node = DriveGearInjector()
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
