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
    for target, keys in aliases.items():
        for key in keys:
            if key in pose:
                env[f"{prefix}_{target.upper()}"] = str(pose[key])
                break
    yaw = pose.get("yaw_rad", pose.get("yaw"))
    if yaw is not None and f"{prefix}_QZ" not in env and f"{prefix}_QW" not in env:
        yaw_rad = float(yaw)
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


def scenario_env(scenario: Dict[str, object]) -> Dict[str, str]:
    if not scenario:
        return {}
    env: Dict[str, str] = {"UTMR_SCENARIO_ID": sanitize_id(str(scenario.get("scenario_id", "scenario")))}
    initial_pose = scenario.get("initial_pose") or scenario.get("localization_pose")
    goal_pose = scenario.get("goal_pose") or scenario.get("goal")
    if isinstance(initial_pose, dict):
        env.update(pose_to_env("UTMR_INIT", initial_pose))
        if "x" in initial_pose or "x_m" in initial_pose:
            env["UTMR_FALLBACK_X"] = str(initial_pose.get("x", initial_pose.get("x_m")))
        if "y" in initial_pose or "y_m" in initial_pose:
            env["UTMR_FALLBACK_Y"] = str(initial_pose.get("y", initial_pose.get("y_m")))
        if "z" in initial_pose or "z_m" in initial_pose:
            env["UTMR_FALLBACK_Z"] = str(initial_pose.get("z", initial_pose.get("z_m")))
        yaw = yaw_from_pose(initial_pose)
        if yaw is not None:
            env["UTMR_FALLBACK_YAW"] = str(yaw)
    if isinstance(goal_pose, dict):
        env.update(pose_to_env("UTMR_GOAL", goal_pose))
    if "route_length_m" in scenario:
        env["UTMR_ROUTE_LENGTH_M"] = str(scenario["route_length_m"])
    if "goal_radius_m" in scenario:
        env["UTMR_GOAL_RADIUS_M"] = str(scenario["goal_radius_m"])
    if "obstacles" in scenario:
        env["UTMR_OBSTACLES_JSON"] = json.dumps(scenario["obstacles"], separators=(",", ":"))
        env["UTMR_STATIC_OBSTACLE_FRAME"] = str(scenario.get("obstacle_frame", "ego"))
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
            "UTMR_COLLISION_TOPIC": env.get("UTMR_COLLISION_TOPIC", "/utmr/collision"),
            "UTMR_COLLISION_OUTPUT_TOPIC": env.get("UTMR_COLLISION_OUTPUT_TOPIC", "/utmr/collision"),
            "RVIZ": env.get("RVIZ", "false"),
            "PERCEPTION": env.get("PERCEPTION", "false"),
            "PLANNING": env.get("PLANNING", "false"),
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


def terminate_processes(processes) -> None:
    for process in reversed([p for p in processes if p is not None]):
        if process.poll() is not None:
            continue
        try:
            os.killpg(process.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
    time.sleep(3.0)
    for process in reversed([p for p in processes if p is not None]):
        if process.poll() is not None:
            continue
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def cleanup_helpers(root: Path, dry_run: bool, phase: str) -> None:
    command = [str(root / "autoware/utmr_scripts/stop_demo_helpers.sh")]
    print(f"[cleanup:{phase}] {' '.join(command)}")
    if not dry_run:
        subprocess.run(command, cwd=str(root), check=False)


def cleanup_autoware_orphans(root: Path, dry_run: bool, phase: str) -> None:
    pattern = str(root / "autoware/install") + "/"
    print(f"[cleanup:{phase}] orphan pattern={pattern}")
    if dry_run:
        return
    subprocess.run(["pkill", "-TERM", "-f", pattern], cwd=str(root), check=False)
    time.sleep(0.5)
    subprocess.run(["pkill", "-KILL", "-f", pattern], cwd=str(root), check=False)


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
        return {"mean_speed_kmh": "", "collision": "False"}
    mean_speed = "" if not speeds else str(sum(speeds) / len(speeds))
    return {"mean_speed_kmh": mean_speed, "collision": str(collision)}


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
        ]
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow(
            {
                "method": METHOD_NAMES[args.variant],
                "variant": args.variant,
                "episode_id": args.episode_id,
                "collision": step_summary["collision"],
                "success": False,
                "timeout": True,
                "distance_m": "",
                "route_length_m": route_length_m,
                "mean_speed_kmh": step_summary["mean_speed_kmh"],
                "driving_score": 0.0,
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

    processes = []
    try:
        cleanup_helpers(root, args.dry_run, "before")
        cleanup_autoware_orphans(root, args.dry_run, "before")
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

        processes.append(
            start_process(
                "utmr_demo",
                [str(root / "autoware/utmr_scripts/run_utmr_demo.sh")],
                root,
                env,
                log_dir / f"{episode_id}_utmr_demo.log",
                args.dry_run,
            )
        )

        if not args.dry_run:
            time.sleep(args.timeout_s)
    finally:
        if not args.dry_run:
            terminate_processes(processes)
            cleanup_helpers(root, args.dry_run, "after")
            cleanup_autoware_orphans(root, args.dry_run, "after")
            if not episode_csv_has_rows(episode_csv):
                write_episode_row(episode_csv, args, step_log)

    print(f"step log: {step_log}")
    print(f"episode csv: {episode_csv}")


if __name__ == "__main__":
    main()
