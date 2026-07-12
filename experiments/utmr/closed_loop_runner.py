#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

from paper_experiments import reduce_experiments, runtime_config_from_params, UTMRParams
from utmr_core import EgoState, Obstacle, UTMRPlanner, build_paper_step_row


VARIANT_MODES = {
    "baseline": "coarse",
    "utmr": "utmr",
    "uniform_fine": "uniform_fine",
    "fine_dt_only": "fine_dt_only",
    "short_horizon_only": "short_horizon_only",
}

METHOD_NAMES = {
    "baseline": "WoTE",
    "utmr": "WoTE + UTMR (Ours)",
    "uniform_fine": "WoTE + Uniform Fine",
    "fine_dt_only": "UTMR (fine dt only)",
    "short_horizon_only": "UTMR (short horizon only)",
}


@dataclass
class EgoWorldState:
    x_m: float
    y_m: float
    yaw_rad: float
    speed_mps: float


@dataclass
class Scenario:
    scenario_id: str
    route_length_m: float
    initial_speed_mps: float
    target_speed_mps: float
    obstacles: List[Obstacle]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a UTMR high-speed closed-loop experiment.")
    parser.add_argument("--out-dir", type=Path, default=Path("experiments/utmr/results/closed_loop"))
    parser.add_argument("--episodes", type=int, default=5)
    parser.add_argument("--timeout-s", type=float, default=30.0)
    parser.add_argument("--planner-rate-hz", type=float, default=10.0)
    parser.add_argument("--variants", nargs="+", default=["baseline", "utmr", "uniform_fine", "fine_dt_only", "short_horizon_only"])
    parser.add_argument("--scenario-file", type=Path)
    parser.add_argument("--seed", type=int, default=20260708)
    parser.add_argument("--analyze", action="store_true")
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


def params_from_args(args: argparse.Namespace) -> UTMRParams:
    return UTMRParams(
        k=args.k,
        coarse_horizon_s=args.coarse_horizon_s,
        coarse_dt_s=args.coarse_dt_s,
        fine_horizon_s=args.fine_horizon_s,
        fine_dt_s=args.fine_dt_s,
        beta=args.beta,
        gamma_h=args.gamma_h,
        gamma_m=args.gamma_m,
        top_n=args.top_n,
        ttc_threshold_s=args.ttc_threshold_s,
    )


def load_scenarios(path: Path | None, episodes: int, seed: int) -> List[Scenario]:
    if path is not None:
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
        return [scenario_from_dict(item) for item in data["scenarios"]]
    return default_scenarios(episodes, seed)


def scenario_from_dict(data: Dict[str, object]) -> Scenario:
    return Scenario(
        scenario_id=str(data["scenario_id"]),
        route_length_m=float(data.get("route_length_m", 1800.0)),
        initial_speed_mps=float(data.get("initial_speed_mps", 25.0)),
        target_speed_mps=float(data.get("target_speed_mps", 36.0)),
        obstacles=[
            Obstacle(float(item["x_m"]), float(item["y_m"]), float(item.get("radius_m", 1.2)), str(item.get("label", "obstacle")))
            for item in data.get("obstacles", [])
        ],
    )


def default_scenarios(episodes: int, seed: int) -> List[Scenario]:
    rng = random.Random(seed)
    scenarios = []
    for idx in range(episodes):
        route_length = rng.uniform(900.0, 1600.0)
        initial_speed = rng.uniform(22.0, 30.0)
        target_speed = rng.uniform(32.0, 40.0)
        obstacles = []
        for obs_idx in range(rng.randint(2, 5)):
            obstacles.append(
                Obstacle(
                    x_m=rng.uniform(180.0, route_length - 80.0),
                    y_m=rng.choice([-1.8, 0.0, 1.8]) + rng.uniform(-0.4, 0.4),
                    radius_m=rng.uniform(0.9, 1.4),
                    label=f"cone_{obs_idx}",
                )
            )
        scenarios.append(
            Scenario(
                scenario_id=f"default_{idx:03d}",
                route_length_m=route_length,
                initial_speed_mps=initial_speed,
                target_speed_mps=target_speed,
                obstacles=obstacles,
            )
        )
    return scenarios


def obstacle_to_ego(obstacle: Obstacle, ego: EgoWorldState) -> Obstacle:
    dx = obstacle.x_m - ego.x_m
    dy = obstacle.y_m - ego.y_m
    cos_yaw = math.cos(-ego.yaw_rad)
    sin_yaw = math.sin(-ego.yaw_rad)
    x_ego = cos_yaw * dx - sin_yaw * dy
    y_ego = sin_yaw * dx + cos_yaw * dy
    return Obstacle(x_ego, y_ego, obstacle.radius_m, obstacle.label)


def collision_in_world(ego: EgoWorldState, obstacles: Sequence[Obstacle], ego_radius_m: float) -> bool:
    for obstacle in obstacles:
        distance = math.hypot(obstacle.x_m - ego.x_m, obstacle.y_m - ego.y_m)
        if distance <= ego_radius_m + obstacle.radius_m:
            return True
    return False


def run_episode(
    planner: UTMRPlanner,
    scenario: Scenario,
    variant: str,
    episode_index: int,
    timeout_s: float,
    planner_rate_hz: float,
) -> tuple[List[Dict[str, object]], Dict[str, object]]:
    dt = 1.0 / planner_rate_hz
    max_steps = int(timeout_s * planner_rate_hz)
    mode = VARIANT_MODES[variant]
    ego = EgoWorldState(x_m=0.0, y_m=0.0, yaw_rad=0.0, speed_mps=scenario.initial_speed_mps)
    speed_samples = []
    step_rows = []
    collision = False
    success = False
    last_result = None

    for step in range(max_steps):
        ego_obstacles = [obstacle_to_ego(obstacle, ego) for obstacle in scenario.obstacles if obstacle.x_m >= ego.x_m - 10.0]
        start = time.perf_counter()
        result = planner.plan(EgoState(speed_mps=ego.speed_mps), mode=mode, obstacles=ego_obstacles)
        latency_ms = (time.perf_counter() - start) * 1000.0
        last_result = result

        row = build_paper_step_row(
            planner=planner,
            ego=EgoState(speed_mps=ego.speed_mps),
            obstacles=ego_obstacles,
            episode_id=f"{variant}_{scenario.scenario_id}_{episode_index}",
            step=step,
        )
        row.update(
            {
                "method_variant": variant,
                "latency_ms": latency_ms,
                "selected_index": result.selected_index,
                "baseline_index": result.baseline_index,
                "triggered": result.triggered,
                "selected_speed_kmh": float(result.candidates.target_speeds_mps[result.selected_index] * 3.6),
            }
        )
        step_rows.append(row)

        target_speed = float(result.candidates.target_speeds_mps[result.selected_index])
        curvature = float(result.candidates.curvatures[result.selected_index])
        acceleration = max(-4.5, min(3.0, (target_speed - ego.speed_mps) / 1.0))
        ego.speed_mps = max(0.0, ego.speed_mps + acceleration * dt)
        ego.yaw_rad += ego.speed_mps * curvature * dt
        ego.x_m += ego.speed_mps * math.cos(ego.yaw_rad) * dt
        ego.y_m += ego.speed_mps * math.sin(ego.yaw_rad) * dt

        if step * dt >= 2.0:
            speed_samples.append(ego.speed_mps * 3.6)

        collision = collision_in_world(ego, scenario.obstacles, planner.config.ego_radius_m)
        success = ego.x_m >= scenario.route_length_m
        if collision or success:
            break

    driving_score = compute_driving_score(success, collision, ego.x_m, scenario.route_length_m, speed_samples, scenario.target_speed_mps)
    episode_row = {
        "method": METHOD_NAMES[variant],
        "variant": variant,
        "scenario_id": scenario.scenario_id,
        "episode_id": f"{variant}_{scenario.scenario_id}_{episode_index}",
        "collision": collision,
        "success": success,
        "timeout": not collision and not success,
        "distance_m": ego.x_m,
        "route_length_m": scenario.route_length_m,
        "mean_speed_kmh": statistics_mean(speed_samples),
        "driving_score": driving_score,
        "triggered": last_result.triggered if last_result else False,
    }
    return step_rows, episode_row


def compute_driving_score(
    success: bool,
    collision: bool,
    distance_m: float,
    route_length_m: float,
    speed_samples_kmh: Sequence[float],
    target_speed_mps: float,
) -> float:
    progress = max(0.0, min(1.0, distance_m / max(route_length_m, 1.0)))
    mean_speed = statistics_mean(speed_samples_kmh)
    speed_target = target_speed_mps * 3.6
    speed_score = 0.0 if math.isnan(mean_speed) else max(0.0, min(1.0, mean_speed / max(speed_target, 1.0)))
    score = 100.0 * (0.55 * progress + 0.25 * speed_score + 0.20 * float(success))
    if collision:
        score *= 0.35
    return score


def statistics_mean(values: Sequence[float]) -> float:
    values = [float(value) for value in values if not math.isnan(float(value))]
    if not values:
        return float("nan")
    return sum(values) / len(values)


def write_jsonl(path: Path, rows: Iterable[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(row, separators=(",", ":")) + "\n")


def write_csv(path: Path, rows: Sequence[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys: List[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    args = parse_args()
    bad_variants = [variant for variant in args.variants if variant not in VARIANT_MODES]
    if bad_variants:
        raise ValueError(f"unknown variants: {bad_variants}")

    params = params_from_args(args)
    planner = UTMRPlanner(runtime_config_from_params(params))
    scenarios = load_scenarios(args.scenario_file, args.episodes, args.seed)

    all_steps: List[Dict[str, object]] = []
    episode_rows: List[Dict[str, object]] = []
    for variant in args.variants:
        for index, scenario in enumerate(scenarios):
            steps, episode = run_episode(
                planner=planner,
                scenario=scenario,
                variant=variant,
                episode_index=index,
                timeout_s=args.timeout_s,
                planner_rate_hz=args.planner_rate_hz,
            )
            all_steps.extend(steps)
            episode_rows.append(episode)

    raw_dir = args.out_dir / "raw"
    steps_path = raw_dir / "closed_loop_steps.jsonl"
    episodes_path = raw_dir / "closed_loop_episodes.csv"
    write_jsonl(steps_path, all_steps)
    write_csv(episodes_path, episode_rows)

    if args.analyze:
        reduce_experiments(all_steps, episode_rows, args.out_dir, params)

    print(f"closed-loop steps: {steps_path}")
    print(f"closed-loop episodes: {episodes_path}")


if __name__ == "__main__":
    main()
