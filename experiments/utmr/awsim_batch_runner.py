#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List

from paper_experiments import load_rows, params_from_args, reduce_experiments


VARIANTS = ["baseline", "utmr", "uniform_fine", "fine_dt_only", "short_horizon_only"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AWSIM/Autoware UTMR variants and analyze combined logs.")
    parser.add_argument("--out-dir", type=Path, default=Path("experiments/utmr/results/awsim_batch"))
    parser.add_argument("--variants", nargs="+", default=VARIANTS)
    parser.add_argument("--episodes", type=int, default=1)
    parser.add_argument("--timeout-s", type=float, default=120.0)
    parser.add_argument("--readiness-timeout-s", type=float, default=180.0)
    parser.add_argument("--startup-delay-s", type=float, default=8.0)
    parser.add_argument("--scenario-file", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-awsim", action="store_true")
    parser.add_argument("--skip-autoware", action="store_true")
    parser.add_argument("--skip-monitor", action="store_true")
    parser.add_argument("--no-analyze", action="store_true")
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


def load_scenario_ids(path: Path | None) -> List[str]:
    if path is None:
        return []
    with path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    scenarios = data if isinstance(data, list) else data.get("scenarios", [])
    return [sanitize_id(str(item.get("scenario_id", f"scenario_{idx:03d}"))) for idx, item in enumerate(scenarios)]


def run_supervisor(root: Path, out_dir: Path, variant: str, episode_idx: int, args: argparse.Namespace) -> None:
    scenario_ids = load_scenario_ids(args.scenario_file)
    scenario_id = scenario_ids[episode_idx % len(scenario_ids)] if scenario_ids else ""
    episode_id = f"{variant}_{scenario_id}_{episode_idx:03d}" if scenario_id else f"{variant}_{episode_idx:03d}"
    command = [
        str(root / "experiments/utmr/run_awsim_supervisor.sh"),
        "--out-dir",
        str(out_dir),
        "--variant",
        variant,
        "--episode-id",
        episode_id,
        "--timeout-s",
        str(args.timeout_s),
        "--readiness-timeout-s",
        str(args.readiness_timeout_s),
        "--startup-delay-s",
        str(args.startup_delay_s),
        "--k",
        str(args.k),
        "--coarse-horizon-s",
        str(args.coarse_horizon_s),
        "--coarse-dt-s",
        str(args.coarse_dt_s),
        "--fine-horizon-s",
        str(args.fine_horizon_s),
        "--fine-dt-s",
        str(args.fine_dt_s),
        "--beta",
        str(args.beta),
        "--gamma-h",
        str(args.gamma_h),
        "--gamma-m",
        str(args.gamma_m),
        "--top-n",
        str(args.top_n),
        "--ttc-threshold-s",
        str(args.ttc_threshold_s),
    ]
    if args.scenario_file:
        command.extend(["--scenario-file", str(args.scenario_file), "--scenario-index", str(episode_idx)])
    if args.dry_run:
        command.append("--dry-run")
    if args.skip_awsim:
        command.append("--skip-awsim")
    if args.skip_autoware:
        command.append("--skip-autoware")
    if args.skip_monitor:
        command.append("--skip-monitor")

    print(" ".join(command))
    subprocess.run(command, cwd=str(root), check=True)


def merge_jsonl(paths: Iterable[Path], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as out_fp:
        for path in paths:
            if not path.exists():
                continue
            with path.open("r", encoding="utf-8") as in_fp:
                for line in in_fp:
                    if line.strip():
                        out_fp.write(line)


def merge_csv(paths: Iterable[Path], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, str]] = []
    fieldnames: List[str] = []
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", newline="") as fp:
            reader = csv.DictReader(fp)
            for name in reader.fieldnames or []:
                if name not in fieldnames:
                    fieldnames.append(name)
            rows.extend(reader)
    with output.open("w", encoding="utf-8", newline="") as fp:
        if not fieldnames:
            return
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    args = parse_args()
    root = utmr_root()
    session_root = args.out_dir / "sessions"
    for variant in args.variants:
        if variant not in VARIANTS:
            raise ValueError(f"unknown variant: {variant}")
        for episode_idx in range(args.episodes):
            run_supervisor(root, session_root / variant / f"episode_{episode_idx:03d}", variant, episode_idx, args)

    if args.dry_run:
        plan_path = args.out_dir / "batch_plan.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        with plan_path.open("w", encoding="utf-8") as fp:
            json.dump(
                {
                    "variants": args.variants,
                    "episodes": args.episodes,
                    "scenario_file": str(args.scenario_file) if args.scenario_file else None,
                    "scenario_ids": load_scenario_ids(args.scenario_file),
                },
                fp,
                indent=2,
            )
        print(f"dry-run plan: {plan_path}")
        return

    step_logs = sorted(session_root.glob("**/raw/*_steps.jsonl"))
    episode_logs = sorted(session_root.glob("**/raw/awsim_episodes.csv"))
    merged_steps = args.out_dir / "raw" / "awsim_batch_steps.jsonl"
    merged_episodes = args.out_dir / "raw" / "awsim_batch_episodes.csv"
    merge_jsonl(step_logs, merged_steps)
    merge_csv(episode_logs, merged_episodes)

    if not args.no_analyze:
        steps = load_rows(merged_steps)
        episodes = load_rows(merged_episodes)
        reduce_experiments(steps, episodes, args.out_dir, params_from_args(args))

    print(f"merged steps: {merged_steps}")
    print(f"merged episodes: {merged_episodes}")


if __name__ == "__main__":
    main()
