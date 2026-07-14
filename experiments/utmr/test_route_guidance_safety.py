#!/usr/bin/env python3
import os
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import rclpy

from experiments.utmr.utmr_core import Obstacle

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "autoware" / "utmr_scripts" / "helpers"))

from utmr_planner_node import UTMRPlannerNode, parse_obstacles, parse_route_points  # noqa: E402


def main() -> None:
    os.environ["UTMR_ROUTE_POINTS_JSON"] = '[{"x": 12.0, "y": 4.0, "z": 0.0}]'
    os.environ["UTMR_ROUTE_LOOKAHEAD_M"] = "12.0"
    os.environ["UTMR_ROUTE_MAX_LATERAL_M"] = "4.0"
    os.environ["UTMR_ROUTE_MAX_YAW_RAD"] = "0.75"

    for bad_route in ("[7]", "[[1.0]]", '[{"x": "nan", "y": 0.0}]'):
        try:
            parse_route_points(bad_route)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad route point was accepted: {bad_route}")

    for bad_obstacles in ('{"x_m": 1.0}', '[{"x_m": 1.0}]', '[{"x_m": 0.0, "y_m": 0.0, "radius_m": "nan"}]'):
        try:
            parse_obstacles(bad_obstacles)
        except ValueError:
            pass
        else:
            raise AssertionError(f"bad obstacle was accepted: {bad_obstacles}")

    with (
        patch.object(UTMRPlannerNode, "create_timer"),
        patch.object(UTMRPlannerNode, "create_subscription"),
        patch.object(UTMRPlannerNode, "create_publisher"),
    ):
        rclpy.init()
        node = UTMRPlannerNode()
        try:
            selected = np.zeros((20, 3), dtype=np.float32)
            selected[:, 0] = np.linspace(1.0, 20.0, 20, dtype=np.float32)
            guided, route_guided = node.apply_route_guidance(selected, (12.0, 4.0))
            assert route_guided

            obstacle_index = 9
            obstacle = Obstacle(
                x_m=float(guided[obstacle_index, 0]),
                y_m=float(guided[obstacle_index, 1]),
                radius_m=1.0,
                label="adversarial_route_guidance",
            )
            reason = node.trajectory_safety_reject_reason(guided, [obstacle])
            assert reason.startswith("collision:"), reason

            safe_reason = node.trajectory_safety_reject_reason(selected, [obstacle])
            assert safe_reason == "", safe_reason

            too_wide = selected.copy()
            too_wide[:, 1] = node.config.lane_half_width_m + 0.5
            wide_reason = node.trajectory_safety_reject_reason(too_wide, [])
            assert wide_reason.startswith("drivability:"), wide_reason

            node.route_max_lateral_m = 20.0
            guided_wide, wide_route_guided = node.apply_route_guidance(selected, (12.0, 20.0))
            assert wide_route_guided
            guided_wide_reason = node.trajectory_safety_reject_reason(guided_wide, [])
            assert guided_wide_reason.startswith("drivability:"), guided_wide_reason

            invalid_obstacle = Obstacle(x_m=0.0, y_m=0.0, radius_m=float("nan"), label="bad_radius")
            invalid_reason = node.trajectory_safety_reject_reason(selected, [invalid_obstacle])
            assert invalid_reason.startswith("obstacle_non_finite:"), invalid_reason

            os.environ["UTMR_ROUTE_LOOKAHEAD_M"] = "nan"
            try:
                UTMRPlannerNode()
            except ValueError as exc:
                assert "UTMR_ROUTE_LOOKAHEAD_M" in str(exc)
            else:
                raise AssertionError("non-finite route lookahead must be rejected")
        finally:
            node.destroy_node()
            rclpy.shutdown()


if __name__ == "__main__":
    main()
