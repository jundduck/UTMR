#!/usr/bin/env python3
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

import rclpy
from autoware_adapi_v1_msgs.msg import MrmState
from autoware_control_msgs.msg import Control
from autoware_perception_msgs.msg import PredictedObjects
from autoware_vehicle_msgs.msg import GearCommand, HazardLightsCommand, TurnIndicatorsCommand
from builtin_interfaces.msg import Time
from helper_shutdown import is_expected_shutdown_error
from nav_msgs.msg import OccupancyGrid
from rclpy._rclpy_pybind11 import RCLError
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from sensor_msgs.msg import PointCloud2, PointField


DEFAULT_OBJECTS_TOPIC: Final = "/perception/object_recognition/objects"
DEFAULT_GRID_TOPIC: Final = "/perception/occupancy_grid_map/map"
DEFAULT_POINTCLOUD_TOPIC: Final = "/perception/obstacle_segmentation/pointcloud"
DEFAULT_EMERGENCY_CONTROL_TOPIC: Final = "/system/emergency/control_cmd"
DEFAULT_EMERGENCY_GEAR_TOPIC: Final = "/system/emergency/gear_cmd"
DEFAULT_EMERGENCY_HAZARD_TOPIC: Final = "/system/emergency/hazard_lights_cmd"
DEFAULT_EMERGENCY_TURN_TOPIC: Final = "/system/emergency/turn_indicators_cmd"
DEFAULT_MRM_STATE_TOPIC: Final = "/system/fail_safe/mrm_state"


@dataclass(frozen=True, slots=True)
class EmptyInputConfig:
    objects_topic: str
    grid_topic: str
    pointcloud_topic: str
    emergency_control_topic: str
    emergency_gear_topic: str
    emergency_hazard_topic: str
    emergency_turn_topic: str
    mrm_state_topic: str
    frame_id: str
    period_s: float
    grid_origin_x: float
    grid_origin_y: float
    grid_resolution: float
    grid_width: int
    grid_height: int
    publish_perception: bool
    publish_emergency: bool
    publish_mrm_state: bool


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    return float(value)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    return int(value)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def read_config() -> EmptyInputConfig:
    return EmptyInputConfig(
        objects_topic=_env_str("AWSIM_EMPTY_OBJECTS_TOPIC", DEFAULT_OBJECTS_TOPIC),
        grid_topic=_env_str("AWSIM_EMPTY_GRID_TOPIC", DEFAULT_GRID_TOPIC),
        pointcloud_topic=_env_str("AWSIM_EMPTY_POINTCLOUD_TOPIC", DEFAULT_POINTCLOUD_TOPIC),
        emergency_control_topic=_env_str(
            "AWSIM_EMPTY_EMERGENCY_CONTROL_TOPIC",
            DEFAULT_EMERGENCY_CONTROL_TOPIC,
        ),
        emergency_gear_topic=_env_str(
            "AWSIM_EMPTY_EMERGENCY_GEAR_TOPIC",
            DEFAULT_EMERGENCY_GEAR_TOPIC,
        ),
        emergency_hazard_topic=_env_str(
            "AWSIM_EMPTY_EMERGENCY_HAZARD_TOPIC",
            DEFAULT_EMERGENCY_HAZARD_TOPIC,
        ),
        emergency_turn_topic=_env_str(
            "AWSIM_EMPTY_EMERGENCY_TURN_TOPIC",
            DEFAULT_EMERGENCY_TURN_TOPIC,
        ),
        mrm_state_topic=_env_str("AWSIM_EMPTY_MRM_STATE_TOPIC", DEFAULT_MRM_STATE_TOPIC),
        frame_id=_env_str("AWSIM_EMPTY_INPUT_FRAME", "map"),
        period_s=_env_float("AWSIM_EMPTY_INPUT_PERIOD_S", 0.2),
        grid_origin_x=_env_float("AWSIM_EMPTY_GRID_ORIGIN_X", 81400.0),
        grid_origin_y=_env_float("AWSIM_EMPTY_GRID_ORIGIN_Y", 49800.0),
        grid_resolution=_env_float("AWSIM_EMPTY_GRID_RESOLUTION", 2.0),
        grid_width=_env_int("AWSIM_EMPTY_GRID_WIDTH", 256),
        grid_height=_env_int("AWSIM_EMPTY_GRID_HEIGHT", 256),
        publish_perception=_env_bool("AWSIM_EMPTY_PUBLISH_PERCEPTION", True),
        publish_emergency=_env_bool("AWSIM_EMPTY_PUBLISH_EMERGENCY", True),
        publish_mrm_state=_env_bool("AWSIM_EMPTY_PUBLISH_MRM_STATE", False),
    )


class EmptySimInputs(Node):
    def __init__(self, config: EmptyInputConfig) -> None:
        super().__init__("utmr_empty_sim_inputs")
        self._config = config
        self._grid_data = [0] * (config.grid_width * config.grid_height)
        self._objects_pub = self.create_publisher(PredictedObjects, config.objects_topic, 10)
        self._grid_pub = self.create_publisher(OccupancyGrid, config.grid_topic, 10)
        self._pointcloud_pub = self.create_publisher(PointCloud2, config.pointcloud_topic, 10)
        self._emergency_control_pub = self.create_publisher(
            Control,
            config.emergency_control_topic,
            10,
        )
        self._emergency_gear_pub = self.create_publisher(GearCommand, config.emergency_gear_topic, 10)
        self._emergency_hazard_pub = self.create_publisher(
            HazardLightsCommand,
            config.emergency_hazard_topic,
            10,
        )
        self._emergency_turn_pub = self.create_publisher(
            TurnIndicatorsCommand,
            config.emergency_turn_topic,
            10,
        )
        self._mrm_state_pub = self.create_publisher(MrmState, config.mrm_state_topic, 10)
        self._timer = self.create_timer(config.period_s, self.publish_once)

    def publish_once(self) -> None:
        stamp = self.get_clock().now().to_msg()
        if self._config.publish_perception:
            self._objects_pub.publish(self._objects_msg(stamp))
            self._grid_pub.publish(self._grid_msg(stamp))
            self._pointcloud_pub.publish(self._pointcloud_msg(stamp))
        if self._config.publish_emergency:
            self._emergency_control_pub.publish(self._emergency_control_msg(stamp))
            self._emergency_gear_pub.publish(self._emergency_gear_msg(stamp))
            self._emergency_hazard_pub.publish(self._emergency_hazard_msg(stamp))
            self._emergency_turn_pub.publish(self._emergency_turn_msg(stamp))
        if self._config.publish_mrm_state:
            self._mrm_state_pub.publish(self._mrm_state_msg(stamp))

    def _objects_msg(self, stamp: Time) -> PredictedObjects:
        msg = PredictedObjects()
        msg.header.stamp = stamp
        msg.header.frame_id = self._config.frame_id
        return msg

    def _grid_msg(self, stamp: Time) -> OccupancyGrid:
        msg = OccupancyGrid()
        msg.header.stamp = stamp
        msg.header.frame_id = self._config.frame_id
        msg.info.resolution = self._config.grid_resolution
        msg.info.width = self._config.grid_width
        msg.info.height = self._config.grid_height
        msg.info.origin.position.x = self._config.grid_origin_x
        msg.info.origin.position.y = self._config.grid_origin_y
        msg.info.origin.orientation.w = 1.0
        msg.data = self._grid_data
        return msg

    def _pointcloud_msg(self, stamp: Time) -> PointCloud2:
        msg = PointCloud2()
        msg.header.stamp = stamp
        msg.header.frame_id = "base_link"
        msg.height = 1
        msg.width = 0
        msg.fields = [
            self._point_field("x", 0),
            self._point_field("y", 4),
            self._point_field("z", 8),
            self._point_field("intensity", 12),
        ]
        msg.is_bigendian = False
        msg.point_step = 16
        msg.row_step = 0
        msg.is_dense = True
        return msg

    def _emergency_control_msg(self, stamp: Time) -> Control:
        msg = Control()
        msg.stamp = stamp
        msg.control_time = stamp
        msg.lateral.stamp = stamp
        msg.lateral.control_time = stamp
        msg.lateral.steering_tire_angle = 0.0
        msg.lateral.steering_tire_rotation_rate = 0.0
        msg.lateral.is_defined_steering_tire_rotation_rate = True
        msg.longitudinal.stamp = stamp
        msg.longitudinal.control_time = stamp
        msg.longitudinal.velocity = 0.0
        msg.longitudinal.acceleration = -1.5
        msg.longitudinal.jerk = 0.0
        msg.longitudinal.is_defined_acceleration = True
        msg.longitudinal.is_defined_jerk = True
        return msg

    def _emergency_gear_msg(self, stamp: Time) -> GearCommand:
        msg = GearCommand()
        msg.stamp = stamp
        msg.command = GearCommand.DRIVE
        return msg

    def _emergency_hazard_msg(self, stamp: Time) -> HazardLightsCommand:
        msg = HazardLightsCommand()
        msg.stamp = stamp
        msg.command = HazardLightsCommand.DISABLE
        return msg

    def _emergency_turn_msg(self, stamp: Time) -> TurnIndicatorsCommand:
        msg = TurnIndicatorsCommand()
        msg.stamp = stamp
        msg.command = TurnIndicatorsCommand.DISABLE
        return msg

    def _mrm_state_msg(self, stamp: Time) -> MrmState:
        msg = MrmState()
        msg.stamp = stamp
        msg.state = MrmState.NORMAL
        msg.behavior = MrmState.NONE
        return msg

    @staticmethod
    def _point_field(name: str, offset: int) -> PointField:
        field = PointField()
        field.name = name
        field.offset = offset
        field.datatype = PointField.FLOAT32
        field.count = 1
        return field


def main() -> None:
    rclpy.init()
    node = EmptySimInputs(read_config())
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    except RCLError as exc:
        if not is_expected_shutdown_error(exc):
            raise
    finally:
        node.destroy_node()
        try:
            if rclpy.ok():
                rclpy.shutdown()
        except RCLError as exc:
            if not is_expected_shutdown_error(exc):
                raise


if __name__ == "__main__":
    main()
