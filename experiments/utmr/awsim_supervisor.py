#!/usr/bin/env python3
# noqa: SIZE_OK - single-file AWSIM episode supervisor for reproducible paper runs.
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Dict, List


METHOD_NAMES = {
    "baseline": "WoTE",
    "utmr": "WoTE + UTMR (Ours)",
    "uniform_fine": "WoTE + Uniform Fine",
    "fine_dt_only": "UTMR (fine dt only)",
    "short_horizon_only": "UTMR (short horizon only)",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one AWSIM/Autoware UTMR experiment session.")
    parser.add_argument("--out-dir", type=Path, default=Path("experiments/utmr/results/awsim_session"))
    parser.add_argument("--variant", choices=sorted(METHOD_NAMES), default="utmr")
    parser.add_argument("--episode-id", default="")
    parser.add_argument("--timeout-s", type=float, default=120.0)
    parser.add_argument("--readiness-timeout-s", type=float, default=180.0)
    parser.add_argument("--skip-awsim", action="store_true")
    parser.add_argument("--skip-autoware", action="store_true")
    parser.add_argument("--skip-monitor", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--startup-delay-s", type=float, default=8.0)
    parser.add_argument("--scenario-file", type=Path)
    parser.add_argument("--scenario-index", type=int, default=0)
    parser.add_argument("--scenario-id")
    parser.add_argument("--k", type=int, default=64)
    parser.add_argument("--coarse-horizon-s", type=float, default=2.0)
    parser.add_argument("--coarse-dt-s", type=float, default=0.20)
    parser.add_argument("--fine-horizon-s", type=float, default=1.0)
    parser.add_argument("--fine-dt-s", type=float, default=0.05)
    parser.add_argument("--beta", type=float, default=1.0)
    parser.add_argument("--gamma-h", type=float, default=0.75)
    parser.add_argument("--gamma-m", type=float, default=0.05)
    parser.add_argument("--top-n", type=int, default=8)
    parser.add_argument("--ttc-threshold-s", type=float, default=1.0)
    return parser.parse_args()


def utmr_root() -> Path:
    return Path(__file__).resolve().parents[2]


def sanitize_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)


def load_scenario(args: argparse.Namespace) -> Dict[str, object]:
    if args.scenario_file is None:
        return {}
    with args.scenario_file.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    scenarios = data if isinstance(data, list) else data.get("scenarios", [])
    if not scenarios:
        raise ValueError(f"scenario file has no scenarios: {args.scenario_file}")
    if args.scenario_id:
        for scenario in scenarios:
            if str(scenario.get("scenario_id", "")) == args.scenario_id:
                return dict(scenario)
        raise ValueError(f"scenario_id not found: {args.scenario_id}")
    return dict(scenarios[args.scenario_index % len(scenarios)])


def pose_to_env(prefix: str, pose: Dict[str, object]) -> Dict[str, str]:
    env: Dict[str, str] = {}
    aliases = {
        "x": ["x", "x_m"],
        "y": ["y", "y_m"],
        "z": ["z", "z_m"],
        "qx": ["qx", "orientation_x"],
        "qy": ["qy", "orientation_y"],
        "qz": ["qz", "orientation_z"],
        "qw": ["qw", "orientation_w"],
    }
    bounds = {
        "x": (-10_000_000.0, 10_000_000.0),
        "y": (-10_000_000.0, 10_000_000.0),
        "z": (-10_000.0, 10_000.0),
        "qx": (-1.1, 1.1),
        "qy": (-1.1, 1.1),
        "qz": (-1.1, 1.1),
        "qw": (-1.1, 1.1),
    }
    for target, keys in aliases.items():
        for key in keys:
            if key in pose:
                lower, upper = bounds[target]
                env[f"{prefix}_{target.upper()}"] = str(finite_float(pose[key], f"{prefix}_{target}", lower, upper))
                break
    yaw = pose.get("yaw_rad", pose.get("yaw"))
    if yaw is not None and f"{prefix}_QZ" not in env and f"{prefix}_QW" not in env:
        yaw_rad = finite_float(yaw, f"{prefix}_yaw", -100.0, 100.0)
        env[f"{prefix}_QX"] = "0.0"
        env[f"{prefix}_QY"] = "0.0"
        env[f"{prefix}_QZ"] = str(math.sin(yaw_rad * 0.5))
        env[f"{prefix}_QW"] = str(math.cos(yaw_rad * 0.5))
    return env


def yaw_from_pose(pose: Dict[str, object]) -> float | None:
    yaw = pose.get("yaw_rad", pose.get("yaw"))
    if yaw is not None:
        return float(yaw)
    try:
        qx = float(pose.get("qx", pose.get("orientation_x", 0.0)))
        qy = float(pose.get("qy", pose.get("orientation_y", 0.0)))
        qz = float(pose.get("qz", pose.get("orientation_z", 0.0)))
        qw = float(pose.get("qw", pose.get("orientation_w", 1.0)))
    except (TypeError, ValueError):
        return None
    siny_cosp = 2.0 * (qw * qz + qx * qy)
    cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
    return math.atan2(siny_cosp, cosy_cosp)


def finite_float(value: object, label: str, lower: float, upper: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric, not boolean")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    if number < lower or number > upper:
        raise ValueError(f"{label}={number} outside [{lower}, {upper}]")
    return number


def scenario_bool(scenario: Dict[str, object], key: str) -> bool:
    if key not in scenario:
        return False
    value = scenario[key]
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a JSON boolean")
    return value


def normalize_route_points(points: list[object]) -> list[Dict[str, float]]:
    normalized: list[Dict[str, float]] = []
    for index, item in enumerate(points):
        if isinstance(item, dict):
            x_value = item.get("x", item.get("x_m"))
            y_value = item.get("y", item.get("y_m"))
            z_value = item.get("z", item.get("z_m", 0.0))
        elif isinstance(item, list):
            values = list(item)
            x_value = values[0] if len(values) > 0 else None
            y_value = values[1] if len(values) > 1 else None
            z_value = values[2] if len(values) > 2 else 0.0
        else:
            raise ValueError(f"route point {index} must be an object or list")
        if x_value is None or y_value is None:
            raise ValueError(f"route point {index} is missing x/y")
        normalized.append(
            {
                "x": finite_float(x_value, f"route_points[{index}].x", -10_000_000.0, 10_000_000.0),
                "y": finite_float(y_value, f"route_points[{index}].y", -10_000_000.0, 10_000_000.0),
                "z": finite_float(z_value, f"route_points[{index}].z", -10_000.0, 10_000.0),
            }
        )
    return normalized


def normalize_obstacles(items: object) -> List[Dict[str, object]]:
    if not isinstance(items, list):
        raise ValueError("obstacles must be a JSON list")
    obstacles: List[Dict[str, object]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"obstacle {index} must be an object")
        x_value = item.get("x_m", item.get("x"))
        y_value = item.get("y_m", item.get("y"))
        if x_value is None or y_value is None:
            raise ValueError(f"obstacle {index} is missing x/y")
        radius_value = item.get("radius_m", item.get("radius", 1.0))
        obstacles.append(
            {
                "x_m": finite_float(x_value, f"obstacles[{index}].x", -10_000_000.0, 10_000_000.0),
                "y_m": finite_float(y_value, f"obstacles[{index}].y", -10_000_000.0, 10_000_000.0),
                "radius_m": finite_float(radius_value, f"obstacles[{index}].radius", 1e-6, 1_000.0),
                "label": str(item.get("label", f"obstacle_{index}")),
            }
        )
    return obstacles


def route_waypoints_yaml(points: list[object], fallback_orientation: Dict[str, object] | None) -> str:
    if not points:
        return "[]"
    orientation = fallback_orientation or {}
    default_qx = finite_float(orientation.get("qx", orientation.get("orientation_x", 0.0)), "route_default_qx", -1.1, 1.1)
    default_qy = finite_float(orientation.get("qy", orientation.get("orientation_y", 0.0)), "route_default_qy", -1.1, 1.1)
    default_qz = finite_float(orientation.get("qz", orientation.get("orientation_z", 0.0)), "route_default_qz", -1.1, 1.1)
    default_qw = finite_float(orientation.get("qw", orientation.get("orientation_w", 1.0)), "route_default_qw", -1.1, 1.1)
    rendered = []
    for index, item in enumerate(points):
        if not isinstance(item, dict):
            raise ValueError("route_waypoints_yaml requires normalized route point dictionaries")
        qx = finite_float(item.get("qx", item.get("orientation_x", default_qx)), f"route_points[{index}].qx", -1.1, 1.1)
        qy = finite_float(item.get("qy", item.get("orientation_y", default_qy)), f"route_points[{index}].qy", -1.1, 1.1)
        qz = finite_float(item.get("qz", item.get("orientation_z", default_qz)), f"route_points[{index}].qz", -1.1, 1.1)
        qw = finite_float(item.get("qw", item.get("orientation_w", default_qw)), f"route_points[{index}].qw", -1.1, 1.1)
        rendered.append(
            "{position: {x: "
            f"{item['x']}, y: {item['y']}, z: {item['z']}"
            "}, orientation: {x: "
            f"{qx}, y: {qy}, z: {qz}, w: {qw}"
            "}}"
        )
    return "[" + ", ".join(rendered) + "]"


def scenario_env(scenario: Dict[str, object]) -> Dict[str, str]:
    if not scenario:
        return {}
    env: Dict[str, str] = {"UTMR_SCENARIO_ID": sanitize_id(str(scenario.get("scenario_id", "scenario")))}
    initial_pose = scenario.get("initial_pose") or scenario.get("localization_pose")
    goal_pose = scenario.get("goal_pose") or scenario.get("goal")
    if isinstance(initial_pose, dict):
        env.update(pose_to_env("UTMR_INIT", initial_pose))
        if "UTMR_INIT_X" in env:
            env["UTMR_FALLBACK_X"] = env["UTMR_INIT_X"]
        if "UTMR_INIT_Y" in env:
            env["UTMR_FALLBACK_Y"] = env["UTMR_INIT_Y"]
        if "UTMR_INIT_Z" in env:
            env["UTMR_FALLBACK_Z"] = env["UTMR_INIT_Z"]
        yaw = yaw_from_pose(initial_pose)
        if yaw is not None:
            env["UTMR_FALLBACK_YAW"] = str(yaw)
    if isinstance(goal_pose, dict):
        env.update(pose_to_env("UTMR_GOAL", goal_pose))
    if "route_length_m" in scenario:
        env["UTMR_ROUTE_LENGTH_M"] = str(finite_float(scenario["route_length_m"], "route_length_m", 0.0, 100_000.0))
    if "goal_radius_m" in scenario:
        env["UTMR_GOAL_RADIUS_M"] = str(finite_float(scenario["goal_radius_m"], "goal_radius_m", 0.0, 1_000.0))
    if "obstacles" in scenario:
        env["UTMR_OBSTACLES_JSON"] = json.dumps(normalize_obstacles(scenario["obstacles"]), separators=(",", ":"))
        env["UTMR_STATIC_OBSTACLE_FRAME"] = str(scenario.get("obstacle_frame", "ego"))
    route_points_raw = scenario.get("route_points", scenario.get("route_waypoints", scenario.get("waypoints")))
    if isinstance(route_points_raw, list):
        route_points = normalize_route_points(route_points_raw)
        env["UTMR_ROUTE_POINTS_JSON"] = json.dumps(route_points, separators=(",", ":"))
        env["UTMR_ROUTE_WAYPOINTS_YAML"] = route_waypoints_yaml(
            route_points,
            goal_pose if isinstance(goal_pose, dict) else None,
        )
        env["UTMR_ACCEPT_ROUTE_ALREADY_SET"] = "0"
    for source_key, env_key in {
        "route_lookahead_m": "UTMR_ROUTE_LOOKAHEAD_M",
        "route_max_lateral_m": "UTMR_ROUTE_MAX_LATERAL_M",
        "route_max_yaw_rad": "UTMR_ROUTE_MAX_YAW_RAD",
    }.items():
        if source_key in scenario:
            bounds = {
                "route_lookahead_m": (1.0, 200.0),
                "route_max_lateral_m": (0.0, 50.0),
                "route_max_yaw_rad": (0.0, 1.57),
            }[source_key]
            env[env_key] = str(finite_float(scenario[source_key], source_key, bounds[0], bounds[1]))
    if scenario_bool(scenario, "allow_synthetic_route_fallback"):
        env["UTMR_ALLOW_SYNTHETIC_ROUTE_FALLBACK"] = "1"
    return env


def command_env(root: Path, args: argparse.Namespace, step_log: Path) -> Dict[str, str]:
    env = os.environ.copy()
    scenario = load_scenario(args)
    env.update(
        {
            "UTMR_MODE": "coarse" if args.variant == "baseline" else args.variant,
            "UTMR_STEP_LOG": str(step_log),
            "UTMR_EPISODE_CSV": str(args.out_dir / "raw" / "awsim_episodes.csv"),
            "UTMR_EPISODE_ID": args.episode_id,
            "UTMR_METHOD": METHOD_NAMES[args.variant],
            "UTMR_VARIANT": args.variant,
            "UTMR_START_METRIC_MONITOR": "0" if args.skip_monitor else "1",
            "UTMR_START_ROUTE_PUBLISHER": env.get("UTMR_START_ROUTE_PUBLISHER", "0"),
            "UTMR_COLLISION_TOPIC": env.get("UTMR_COLLISION_TOPIC", "/utmr/collision"),
            "UTMR_COLLISION_OUTPUT_TOPIC": env.get("UTMR_COLLISION_OUTPUT_TOPIC", "/utmr/collision"),
            "RVIZ": env.get("RVIZ", "false"),
            "PERCEPTION": env.get("PERCEPTION", "false"),
            "PLANNING": env.get("PLANNING", "false"),
            "UTMR_DISABLE_AUTOMATIC_POSE_INITIALIZER": env.get(
                "UTMR_DISABLE_AUTOMATIC_POSE_INITIALIZER", "1"
            ),
            "UTMR_K": str(args.k),
            "UTMR_COARSE_HORIZON_S": str(args.coarse_horizon_s),
            "UTMR_COARSE_DT_S": str(args.coarse_dt_s),
            "UTMR_FINE_HORIZON_S": str(args.fine_horizon_s),
            "UTMR_FINE_DT_S": str(args.fine_dt_s),
            "UTMR_BETA": str(args.beta),
            "UTMR_GAMMA_H": str(args.gamma_h),
            "UTMR_GAMMA_M": str(args.gamma_m),
            "UTMR_TOP_N": str(args.top_n),
            "UTMR_TTC_THRESHOLD_S": str(args.ttc_threshold_s),
        }
    )
    env.update(scenario_env(scenario))
    return env


def start_process(name: str, command: List[str], cwd: Path, env: Dict[str, str], log_path: Path, dry_run: bool):
    printable = " ".join(command)
    print(f"[{name}] {printable}")
    if dry_run:
        return None
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8")
    return subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def wait_for_readiness(process, timeout_s: float) -> None:
    if process is None:
        return
    deadline = time.monotonic() + timeout_s
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.5)
    if process.poll() is None:
        raise TimeoutError(f"UTMR readiness did not finish within {timeout_s:.1f}s")
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, "run_utmr_demo.sh")


def terminate_processes(processes) -> None:
    for process in reversed([p for p in processes if p is not None]):
        try:
            os.killpg(process.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
    time.sleep(3.0)
    for process in reversed([p for p in processes if p is not None]):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    time.sleep(2.0)
    for process in reversed([p for p in processes if p is not None]):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def cleanup_helpers(root: Path, env: Dict[str, str], dry_run: bool, phase: str) -> None:
    command = [str(root / "autoware/utmr_scripts/stop_demo_helpers.sh")]
    print(f"[cleanup:{phase}] {' '.join(command)}")
    if not dry_run:
        subprocess.run(command, cwd=str(root), env=env, check=False)


def summarize_step_log(path: Path) -> Dict[str, str]:
    speeds: List[float] = []
    collision = False
    try:
        with path.open("r", encoding="utf-8") as fp:
            for line in fp:
                if not line.strip():
                    continue
                row = json.loads(line)
                if "ego_speed_kmh" in row:
                    speeds.append(float(row["ego_speed_kmh"]))
                collision_mask = row.get("collision_mask", [])
                if isinstance(collision_mask, list) and any(bool(value) for value in collision_mask):
                    collision = True
    except FileNotFoundError:
        return {"planner_mean_speed_kmh": "", "predicted_collision": "False"}
    mean_speed = "" if not speeds else str(sum(speeds) / len(speeds))
    return {"planner_mean_speed_kmh": mean_speed, "predicted_collision": str(collision)}


def episode_csv_has_rows(path: Path) -> bool:
    if not path.exists():
        return False
    with path.open("r", encoding="utf-8") as fp:
        return sum(1 for _ in fp) > 1


def write_episode_row(path: Path, args: argparse.Namespace, step_log: Path) -> None:
    step_summary = summarize_step_log(step_log)
    scenario = load_scenario(args)
    route_length_m = scenario.get("route_length_m", "")
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as fp:
        fieldnames = [
            "method",
            "variant",
            "episode_id",
            "collision",
            "success",
            "timeout",
            "distance_m",
            "route_length_m",
            "mean_speed_kmh",
            "driving_score",
            "metric_source",
            "metric_note",
        ]
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow(
            {
                "method": METHOD_NAMES[args.variant],
                "variant": args.variant,
                "episode_id": args.episode_id,
                "collision": "",
                "success": False,
                "timeout": True,
                "distance_m": "",
                "route_length_m": route_length_m,
                "mean_speed_kmh": "",
                "driving_score": "",
                "metric_source": "fallback",
                "metric_note": (
                    "episode_metric_monitor did not flush; "
                    f"planner_mean_speed_kmh={step_summary['planner_mean_speed_kmh']}; "
                    f"predicted_collision={step_summary['predicted_collision']}"
                ),
            }
        )


def main() -> None:
    args = parse_args()
    root = utmr_root()
    if not args.out_dir.is_absolute():
        args.out_dir = root / args.out_dir
    scenario = load_scenario(args)
    scenario_id = sanitize_id(str(scenario.get("scenario_id", ""))) if scenario else ""
    episode_id = args.episode_id or f"{args.variant}_{scenario_id or int(time.time())}"
    args.episode_id = episode_id

    raw_dir = args.out_dir / "raw"
    log_dir = args.out_dir / "process_logs"
    step_log = raw_dir / f"{episode_id}_steps.jsonl"
    episode_csv = raw_dir / "awsim_episodes.csv"
    env = command_env(root, args, step_log)
    env["UTMR_HELPER_LOG_DIR"] = str(log_dir)
    env["UTMR_HELPER_PID_DIR"] = str(log_dir / "helper_pids")

    processes = []
    try:
        cleanup_helpers(root, env, args.dry_run, "before")
        if not args.skip_awsim:
            processes.append(
                start_process(
                    "awsim",
                    [str(root / "autoware/utmr_scripts/run_awsim.sh")],
                    root,
                    env,
                    log_dir / f"{episode_id}_awsim.log",
                    args.dry_run,
                )
            )
            if not args.dry_run:
                time.sleep(args.startup_delay_s)

        if not args.skip_autoware:
            processes.append(
                start_process(
                    "autoware",
                    [str(root / "autoware/utmr_scripts/launch_autoware_e2e.sh")],
                    root,
                    env,
                    log_dir / f"{episode_id}_autoware.log",
                    args.dry_run,
                )
            )
            if not args.dry_run:
                time.sleep(args.startup_delay_s)

        demo_process = start_process(
            "utmr_demo",
            [str(root / "autoware/utmr_scripts/run_utmr_demo.sh")],
            root,
            env,
            log_dir / f"{episode_id}_utmr_demo.log",
            args.dry_run,
        )
        processes.append(demo_process)

        if not args.dry_run:
            wait_for_readiness(demo_process, args.readiness_timeout_s)
            time.sleep(args.timeout_s)
    finally:
        if not args.dry_run:
            terminate_processes(processes)
            cleanup_helpers(root, env, args.dry_run, "after")
            if not episode_csv_has_rows(episode_csv):
                write_episode_row(episode_csv, args, step_log)

    print(f"step log: {step_log}")
    print(f"episode csv: {episode_csv}")


if __name__ == "__main__":
    main()
