#!/usr/bin/env python3
import json
import math
import os

import rclpy
from geometry_msgs.msg import TransformStamped
from helper_shutdown import is_expected_shutdown_error
from rclpy._rclpy_pybind11 import RCLError
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.parameter import Parameter
from tf2_ros import StaticTransformBroadcaster
from tf2_ros import TransformBroadcaster


DEFAULT_TRANSFORMS = [
    {
        "parent": "base_link",
        "child": "tamagawa/imu_link",
        "xyz": [0.0, 0.0, 1.5],
        "rpy": [0.0, 0.0, 0.0],
    },
    {
        "parent": "base_link",
        "child": "velodyne_top",
        "xyz": [0.0, 0.0, 2.0],
        "rpy": [0.0, 0.0, 0.0],
    },
]


def quaternion_from_rpy(roll: float, pitch: float, yaw: float) -> tuple[float, float, float, float]:
    cy = math.cos(yaw * 0.5)
    sy = math.sin(yaw * 0.5)
    cp = math.cos(pitch * 0.5)
    sp = math.sin(pitch * 0.5)
    cr = math.cos(roll * 0.5)
    sr = math.sin(roll * 0.5)
    return (
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def load_transforms() -> list[dict]:
    raw = os.environ.get("UTMR_STATIC_TF_JSON")
    if not raw:
        return DEFAULT_TRANSFORMS
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("UTMR_STATIC_TF_JSON must be a JSON list")
    return data


class StaticTfInjector(Node):
    def __init__(self):
        super().__init__("utmr_static_tf_injector")
        self.set_parameters([Parameter("use_sim_time", Parameter.Type.BOOL, True)])
        self.static_broadcaster = StaticTransformBroadcaster(self)
        self.dynamic_broadcaster = TransformBroadcaster(self)
        self.transforms = [self.make_transform(spec) for spec in load_transforms()]
        period_s = float(os.environ.get("UTMR_STATIC_TF_PERIOD_S", "0.05"))
        self.timer = self.create_timer(period_s, self.publish_transforms)
        self.count = 0
        self.publish_static_transforms()

    def make_transform(self, spec: dict) -> TransformStamped:
        parent = str(spec["parent"])
        child = str(spec["child"])
        xyz = spec.get("xyz", [0.0, 0.0, 0.0])
        if len(xyz) != 3:
            raise ValueError(f"xyz must have length 3 for {parent}->{child}")

        if "quat" in spec:
            quat = spec["quat"]
            if len(quat) != 4:
                raise ValueError(f"quat must have length 4 for {parent}->{child}")
            qx, qy, qz, qw = [float(value) for value in quat]
        else:
            rpy = spec.get("rpy", [0.0, 0.0, 0.0])
            if len(rpy) != 3:
                raise ValueError(f"rpy must have length 3 for {parent}->{child}")
            qx, qy, qz, qw = quaternion_from_rpy(*(float(value) for value in rpy))

        transform = TransformStamped()
        transform.header.frame_id = parent
        transform.child_frame_id = child
        transform.transform.translation.x = float(xyz[0])
        transform.transform.translation.y = float(xyz[1])
        transform.transform.translation.z = float(xyz[2])
        transform.transform.rotation.x = qx
        transform.transform.rotation.y = qy
        transform.transform.rotation.z = qz
        transform.transform.rotation.w = qw
        return transform

    def publish_static_transforms(self):
        stamp = self.get_clock().now().to_msg()
        for transform in self.transforms:
            transform.header.stamp = stamp
        self.static_broadcaster.sendTransform(self.transforms)
        self.count += 1
        if self.count == 1 or self.count % 10 == 0:
            frames = ", ".join(f"{tf.header.frame_id}->{tf.child_frame_id}" for tf in self.transforms)
            self.get_logger().info(f"published static transforms #{self.count}: {frames}")

    def publish_transforms(self):
        stamp = self.get_clock().now().to_msg()
        for transform in self.transforms:
            transform.header.stamp = stamp
        self.dynamic_broadcaster.sendTransform(self.transforms)
        self.count += 1
        if self.count == 1 or self.count % 200 == 0:
            frames = ", ".join(f"{tf.header.frame_id}->{tf.child_frame_id}" for tf in self.transforms)
            self.get_logger().info(f"published dynamic transforms #{self.count}: {frames}")


def main():
    rclpy.init()
    node = StaticTfInjector()
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
