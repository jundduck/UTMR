#!/usr/bin/env python3
# noqa: SIZE_OK - single-file paper experiment CLI/reducer kept for reproducibility.
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class UTMRParams:
    k: int = 64
    coarse_horizon_s: float = 2.0
    coarse_dt_s: float = 0.20
    fine_horizon_s: float = 1.0
    fine_dt_s: float = 0.05
    beta: float = 1.0
    gamma_h: float = 0.75
    gamma_m: float = 0.05
    top_n: int = 8
    ttc_threshold_s: float = 1.0
    min_nc_score: float = 0.5
    min_ttc_score: float = 0.5


VARIANTS = {
    "wote_coarse": {
        "label": "WoTE (coarse)",
        "fine_keys": [],
        "uniform": False,
    },
    "wote_utmr": {
        "label": "WoTE + UTMR (Full)",
        "fine_keys": ["fine_scores_full", "fine_scores"],
        "uniform": False,
    },
    "wote_uniform_fine": {
        "label": "WoTE + Uniform Fine",
        "fine_keys": ["fine_scores_full", "fine_scores"],
        "uniform": True,
    },
    "utmr_fine_dt_only": {
        "label": "UTMR (fine dt only)",
        "fine_keys": ["fine_dt_scores", "fine_scores_full", "fine_scores"],
        "uniform": False,
    },
    "utmr_short_horizon_only": {
        "label": "UTMR (short horizon only)",
        "fine_keys": ["short_horizon_scores", "fine_scores_full", "fine_scores"],
        "uniform": False,
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run and reduce the UTMR paper experiments.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Reduce step/episode logs into paper tables and figures.")
    analyze.add_argument("--steps", type=Path, required=True, help="JSONL step score log.")
    analyze.add_argument("--episodes", type=Path, help="CSV or JSONL episode-level metrics.")
    analyze.add_argument("--out-dir", type=Path, required=True)
    add_param_args(analyze)

    smoke = subparsers.add_parser("smoke", help="Generate deterministic toy logs and run every reducer.")
    smoke.add_argument("--out-dir", type=Path, default=Path("experiments/utmr/results/smoke"))
    add_param_args(smoke)

    plan = subparsers.add_parser("plan", help="Write the paper experiment matrix.")
    plan.add_argument("--out-dir", type=Path, default=Path("experiments/utmr/results/plan"))
    add_param_args(plan)

    anchors = subparsers.add_parser("export-anchors", help="Export a K=64 cubic-spline anchor set.")
    anchors.add_argument("--output", type=Path, default=Path("third_party/WoTE/dataset/extra_data/planning_vb/trajectory_anchors_64.npy"))
    anchors.add_argument("--anchor-speed-kmh", type=float, default=108.0)
    add_param_args(anchors)

    navsim = subparsers.add_parser("run-navsim-suite", help="Run available WoTE/NAVSIM variants.")
    navsim.add_argument("--out-dir", type=Path, default=Path("experiments/utmr/results/navsim"))
    navsim.add_argument("--wrapper", type=Path, default=Path("experiments/utmr/run_navsim_wote_eval.sh"))
    navsim.add_argument("--dry-run", action="store_true")
    navsim.add_argument("hydra_args", nargs=argparse.REMAINDER)
    add_param_args(navsim)

    return parser.parse_args()


def add_param_args(parser: argparse.ArgumentParser) -> None:
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


def runtime_config_from_params(params: UTMRParams):
    from utmr_core import UTMRRuntimeConfig

    return UTMRRuntimeConfig(
        k=params.k,
        coarse_horizon_s=params.coarse_horizon_s,
        coarse_dt_s=params.coarse_dt_s,
        fine_horizon_s=params.fine_horizon_s,
        fine_dt_s=params.fine_dt_s,
        beta=params.beta,
        gamma_h=params.gamma_h,
        gamma_m=params.gamma_m,
        top_n=params.top_n,
        ttc_threshold_s=params.ttc_threshold_s,
    )


def coerce_scalar(value: str) -> Any:
    text = value.strip()
    if text == "":
        return None
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    if text.startswith("[") or text.startswith("{"):
        return json.loads(text)
    try:
        if any(ch in text for ch in [".", "e", "E"]):
            return float(text)
        return int(text)
    except ValueError:
        return text


def load_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".jsonl":
        rows = []
        with path.open("r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows
    with path.open("r", encoding="utf-8", newline="") as fp:
        return [{k: coerce_scalar(v) for k, v in row.items()} for row in csv.DictReader(fp)]


def write_csv(path: Path, rows: Sequence[Dict[str, Any]], fieldnames: Optional[Sequence[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        keys: List[str] = []
        for row in rows:
            for key in row.keys():
                if key not in keys:
                    keys.append(key)
        fieldnames = keys
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: format_cell(row.get(key)) for key in fieldnames})


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(json_clean(data), fp, indent=2, sort_keys=True, allow_nan=False)
        fp.write("\n")


def json_clean(data: Any) -> Any:
    if isinstance(data, float):
        return None if math.isnan(data) or math.isinf(data) else data
    if isinstance(data, dict):
        return {key: json_clean(value) for key, value in data.items()}
    if isinstance(data, list):
        return [json_clean(value) for value in data]
    return data


def write_markdown(path: Path, rows: Sequence[Dict[str, Any]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        fp.write("| " + " | ".join(fieldnames) + " |\n")
        fp.write("| " + " | ".join(["---"] * len(fieldnames)) + " |\n")
        for row in rows:
            fp.write("| " + " | ".join(format_cell(row.get(key)) for key in fieldnames) + " |\n")


def format_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        return f"{value:.4g}"
    if isinstance(value, (list, dict)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def as_float_list(value: Any) -> List[float]:
    if value is None:
        return []
    if isinstance(value, str):
        value = json.loads(value)
    return [float(item) for item in value]


def as_bool_list(value: Any) -> List[bool]:
    if value is None:
        return []
    if isinstance(value, str):
        value = json.loads(value)
    return [bool(item) for item in value]


def as_matrix(value: Any) -> List[List[float]]:
    if value is None:
        return []
    if isinstance(value, str):
        value = json.loads(value)
    return [[float(item) for item in row] for row in value]


def get_first(row: Dict[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def normalize_scores(scores: Sequence[float]) -> List[float]:
    if not scores:
        return []
    low = min(scores)
    high = max(scores)
    if abs(high - low) < 1e-12:
        return [0.0 for _ in scores]
    return [(score - low) / (high - low) for score in scores]


def entropy_and_margin(scores: Sequence[float], beta: float) -> Tuple[float, float]:
    normalized = normalize_scores(scores)
    if not normalized:
        return float("nan"), float("nan")
    max_logit = max(normalized) / beta
    exps = [math.exp((score / beta) - max_logit) for score in normalized]
    total = sum(exps)
    probabilities = [value / total for value in exps]
    entropy = -sum(p * math.log(max(p, 1e-12)) for p in probabilities) / math.log(len(probabilities))
    top2 = sorted(normalized, reverse=True)[:2]
    margin = top2[0] - top2[1] if len(top2) >= 2 else float("inf")
    return entropy, margin


def masked_argmax(scores: Sequence[float], feasible: Sequence[bool]) -> int:
    best_idx = -1
    best_score = -float("inf")
    for idx, score in enumerate(scores):
        if idx < len(feasible) and feasible[idx] and score > best_score:
            best_idx = idx
            best_score = score
    if best_idx >= 0:
        return best_idx
    return max(range(len(scores)), key=lambda index: scores[index])


def top_n_indices(scores: Sequence[float], feasible: Sequence[bool], n: int) -> List[int]:
    candidates = [
        (idx, score)
        for idx, score in enumerate(scores)
        if idx < len(feasible) and feasible[idx]
    ]
    candidates.sort(key=lambda item: item[1], reverse=True)
    return [idx for idx, _ in candidates[:n]]


def feasible_mask(row: Dict[str, Any], params: UTMRParams, k: int) -> List[bool]:
    explicit = get_first(row, ["feasible_mask", "coarse_feasible_mask"])
    if explicit is not None:
        mask = as_bool_list(explicit)
        return pad_bool(mask, k, True)

    mask = [True] * k
    collision_mask = get_first(row, ["collision_mask", "predicted_collision_mask"])
    if collision_mask is not None:
        collisions = pad_bool(as_bool_list(collision_mask), k, False)
        mask = [ok and not hit for ok, hit in zip(mask, collisions)]

    ttc = get_first(row, ["ttc_s", "coarse_ttc_s"])
    if ttc is not None:
        ttc_values = pad_float(as_float_list(ttc), k, float("inf"))
        mask = [ok and value >= params.ttc_threshold_s for ok, value in zip(mask, ttc_values)]

    sim_rewards = get_first(row, ["sim_rewards"])
    if sim_rewards is not None:
        rewards = as_matrix(sim_rewards)
        if len(rewards) >= 4:
            nc = pad_float(rewards[0], k, 1.0)
            ttc_score = pad_float(rewards[3], k, 1.0)
            mask = [
                ok and nc_score >= params.min_nc_score and ttc_value >= params.min_ttc_score
                for ok, nc_score, ttc_value in zip(mask, nc, ttc_score)
            ]

    return mask


def pad_bool(values: Sequence[bool], length: int, default: bool) -> List[bool]:
    padded = list(values[:length])
    padded.extend([default] * (length - len(padded)))
    return padded


def pad_float(values: Sequence[float], length: int, default: float) -> List[float]:
    padded = list(values[:length])
    padded.extend([default] * (length - len(padded)))
    return padded


def candidate_speeds(row: Dict[str, Any], k: int) -> List[float]:
    speeds = get_first(row, ["candidate_speeds_kmh", "candidate_speeds"])
    if speeds is not None:
        return pad_float(as_float_list(speeds), k, float("nan"))
    trajectories = get_first(row, ["candidate_trajectories", "all_trajectory"])
    if trajectories is None:
        return [float("nan")] * k
    if isinstance(trajectories, str):
        trajectories = json.loads(trajectories)
    interval = float(row.get("trajectory_dt_s", 0.5) or 0.5)
    result = []
    for trajectory in trajectories[:k]:
        if len(trajectory) < 2:
            result.append(0.0)
            continue
        distance = 0.0
        for prev, cur in zip(trajectory, trajectory[1:]):
            distance += math.hypot(float(cur[0]) - float(prev[0]), float(cur[1]) - float(prev[1]))
        result.append((distance / max(len(trajectory) - 1, 1) / interval) * 3.6)
    return pad_float(result, k, float("nan"))


def find_fine_scores(row: Dict[str, Any], variant: str) -> Tuple[List[float], str]:
    for key in VARIANTS[variant]["fine_keys"]:
        value = get_first(row, [key])
        if value is not None:
            return as_float_list(value), key
    return [], ""


def select_variant(row: Dict[str, Any], variant: str, params: UTMRParams) -> Dict[str, Any]:
    coarse = as_float_list(get_first(row, ["coarse_scores", "final_rewards"]))
    if not coarse:
        raise ValueError("step row is missing coarse_scores/final_rewards")
    k = len(coarse)
    feasible = feasible_mask(row, params, k)
    speeds = candidate_speeds(row, k)
    entropy, margin = entropy_and_margin(coarse, params.beta)
    baseline_idx = masked_argmax(coarse, feasible)
    triggered = entropy > params.gamma_h or margin < params.gamma_m
    if variant == "wote_coarse":
        triggered = False
    if VARIANTS[variant]["uniform"]:
        triggered = True

    selected_idx = baseline_idx
    fine_source = ""
    fine_available = False
    if variant != "wote_coarse" and triggered:
        fine_scores, fine_source = find_fine_scores(row, variant)
        if fine_scores:
            fine_available = True
            fine_scores = pad_float(fine_scores, k, -float("inf"))
            rerank_candidates = top_n_indices(coarse, feasible, min(params.top_n, k))
            if rerank_candidates:
                selected_idx = max(rerank_candidates, key=lambda index: fine_scores[index])

    load = params.k * params.coarse_horizon_s / params.coarse_dt_s
    if variant != "wote_coarse" and triggered:
        load += params.top_n * params.fine_horizon_s / params.fine_dt_s

    selected_speed = speeds[selected_idx] if 0 <= selected_idx < len(speeds) else float("nan")
    baseline_speed = speeds[baseline_idx] if 0 <= baseline_idx < len(speeds) else float("nan")
    feasible_speeds = [speed for speed, ok in zip(speeds, feasible) if ok and not math.isnan(speed)]
    oracle_speed = max(feasible_speeds) if feasible_speeds else float("nan")

    return {
        "variant": variant,
        "method": VARIANTS[variant]["label"],
        "step": row.get("step"),
        "episode_id": row.get("episode_id", row.get("token")),
        "entropy": entropy,
        "margin": margin,
        "triggered": triggered,
        "fine_available": fine_available,
        "fine_source": fine_source,
        "baseline_index": baseline_idx,
        "selected_index": selected_idx,
        "selected_speed_kmh": selected_speed,
        "baseline_speed_kmh": baseline_speed,
        "oracle_speed_kmh": oracle_speed,
        "speed_gap_kmh": oracle_speed - baseline_speed if not math.isnan(oracle_speed) else float("nan"),
        "ego_speed_kmh": float(row.get("ego_speed_kmh", selected_speed) or 0.0),
        "feasible_count": sum(1 for ok in feasible if ok),
        "eval_load_elements": load,
        "latency_ms": latency_for_variant(row, variant),
    }


def latency_for_variant(row: Dict[str, Any], variant: str) -> float:
    latency_by_variant = row.get("latency_ms_by_variant")
    if latency_by_variant:
        if isinstance(latency_by_variant, str):
            latency_by_variant = json.loads(latency_by_variant)
        return to_float(latency_by_variant.get(variant))

    method_variant = str(row.get("method_variant", "") or "")
    if method_variant:
        aliases = {
            "wote_coarse": {"baseline", "coarse", "wote_coarse", "WoTE", "WoTE (coarse)"},
            "wote_utmr": {"utmr", "wote_utmr", "WoTE + UTMR (Ours)", "WoTE + UTMR (Full)"},
            "wote_uniform_fine": {"uniform_fine", "wote_uniform_fine", "WoTE + Uniform Fine"},
            "utmr_fine_dt_only": {"fine_dt_only", "utmr_fine_dt_only", "UTMR (fine dt only)"},
            "utmr_short_horizon_only": {"short_horizon_only", "utmr_short_horizon_only", "UTMR (short horizon only)"},
        }
        if method_variant not in aliases.get(variant, set()):
            return float("nan")
    return to_float(row.get("latency_ms"))


def reduce_experiments(steps: List[Dict[str, Any]], episodes: List[Dict[str, Any]], out_dir: Path, params: UTMRParams) -> Dict[str, Any]:
    tables = out_dir / "tables"
    figures = out_dir / "figures"
    raw = out_dir / "raw"
    for directory in [tables, figures, raw]:
        directory.mkdir(parents=True, exist_ok=True)

    selections = []
    for row in steps:
        for variant in VARIANTS:
            selections.append(select_variant(row, variant, params))
    write_csv(raw / "step_selections.csv", selections)

    observed_episodes = metric_monitor_episodes(episodes)
    summary: Dict[str, Any] = {
        "params": asdict(params),
        "num_steps": len(steps),
        "num_episodes": len(episodes),
        "num_observed_episodes": len(observed_episodes),
        "num_fallback_episodes": len(episodes) - len(observed_episodes),
    }
    summary["main_closed_loop"] = reduce_episode_table(observed_episodes, tables)
    summary["runtime"] = reduce_runtime_table(selections, observed_episodes, tables)
    summary["ablation"] = reduce_ablation_tables(selections, observed_episodes, tables)
    summary["speed_uncertainty"] = reduce_speed_uncertainty(selections, figures)
    summary["selection_bias"] = reduce_selection_bias(selections, figures)
    summary["qualitative"] = reduce_qualitative(steps, selections, figures, params)
    write_json(out_dir / "summary.json", summary)
    return summary


def metric_monitor_episodes(episodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [row for row in episodes if str(row.get("metric_source", "observed")).lower() != "fallback"]


def mean_std(values: Sequence[float]) -> Tuple[float, float]:
    values = [value for value in values if value is not None and not math.isnan(float(value))]
    if not values:
        return float("nan"), float("nan")
    if len(values) == 1:
        return float(values[0]), 0.0
    return float(statistics.mean(values)), float(statistics.stdev(values))


def truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "y"}
    return bool(value)


def to_float(value: Any, default: float = float("nan")) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def reduce_episode_table(episodes: List[Dict[str, Any]], tables: Path) -> List[Dict[str, Any]]:
    fieldnames = [
        "method",
        "episodes",
        "collision_pct_mean",
        "collision_pct_std",
        "success_pct_mean",
        "success_pct_std",
        "mean_speed_kmh_mean",
        "mean_speed_kmh_std",
        "driving_score_mean",
        "driving_score_std",
    ]
    if not episodes:
        write_csv(tables / "table_i_main_closed_loop.csv", [], fieldnames)
        write_markdown(tables / "table_i_main_closed_loop.md", [], fieldnames)
        return []
    grouped = group_by(episodes, "method")
    rows = []
    for method, items in grouped.items():
        collision_values = [100.0 * float(truthy(item.get("collision", False))) for item in items]
        success_values = [100.0 * float(truthy(item.get("success", False))) for item in items]
        speed_values = [to_float(item.get("mean_speed_kmh", item.get("mean_speed"))) for item in items]
        score_values = [to_float(item.get("driving_score", item.get("score"))) for item in items]
        collision_mean, collision_std = mean_std(collision_values)
        success_mean, success_std = mean_std(success_values)
        speed_mean, speed_std = mean_std(speed_values)
        score_mean, score_std = mean_std(score_values)
        rows.append(
            {
                "method": method,
                "episodes": len(items),
                "collision_pct_mean": collision_mean,
                "collision_pct_std": collision_std,
                "success_pct_mean": success_mean,
                "success_pct_std": success_std,
                "mean_speed_kmh_mean": speed_mean,
                "mean_speed_kmh_std": speed_std,
                "driving_score_mean": score_mean,
                "driving_score_std": score_std,
            }
        )
    order = ["VAD", "WoTE", "WoTE + UTMR (Ours)", "WoTE + Uniform Fine"]
    rows.sort(key=lambda row: order.index(row["method"]) if row["method"] in order else len(order))
    write_csv(tables / "table_i_main_closed_loop.csv", rows, fieldnames)
    write_markdown(tables / "table_i_main_closed_loop.md", rows, fieldnames)
    return rows


def reduce_runtime_table(selections: List[Dict[str, Any]], episodes: List[Dict[str, Any]], tables: Path) -> List[Dict[str, Any]]:
    grouped = group_by(selections, "variant")
    pdm_by_method = pdm_score_by_method(episodes)
    rows = []
    for variant in ["wote_coarse", "wote_utmr", "wote_uniform_fine"]:
        items = grouped.get(variant, [])
        if not items:
            continue
        trigger_rate = 100.0 * sum(1 for item in items if item["triggered"]) / len(items)
        load_mean, _ = mean_std([float(item["eval_load_elements"]) for item in items])
        latency_values = [to_float(item.get("latency_ms")) for item in items if item.get("latency_ms")]
        latency_mean, _ = mean_std(latency_values)
        p99 = percentile(latency_values, 99.0)
        rows.append(
            {
                "method": VARIANTS[variant]["label"],
                "trigger_rate_pct": trigger_rate,
                "latency_ms_mean": latency_mean,
                "latency_ms_p99": p99,
                "eval_load_elements_mean": load_mean,
                "pdm_score": lookup_pdm_score(pdm_by_method, variant),
            }
        )
    fieldnames = [
        "method",
        "trigger_rate_pct",
        "latency_ms_mean",
        "latency_ms_p99",
        "eval_load_elements_mean",
        "pdm_score",
    ]
    write_csv(tables / "table_ii_runtime.csv", rows, fieldnames)
    write_markdown(tables / "table_ii_runtime.md", rows, fieldnames)
    return rows


def pdm_score_by_method(episodes: List[Dict[str, Any]]) -> Dict[str, float]:
    grouped = group_by(episodes, "method")
    result = {}
    for method, items in grouped.items():
        scores = [to_float(item.get("pdm_score", item.get("score"))) for item in items]
        result[method] = mean_std(scores)[0]
    return result


def lookup_pdm_score(scores: Dict[str, float], variant: str) -> float:
    aliases = {
        "wote_coarse": ["WoTE (coarse)", "WoTE"],
        "wote_utmr": ["WoTE + UTMR (Full)", "WoTE + UTMR (Ours)", "UTMR (Full)"],
        "wote_uniform_fine": ["WoTE + Uniform Fine"],
    }
    for method in aliases.get(variant, [VARIANTS[variant]["label"]]):
        value = scores.get(method)
        if value is not None and not math.isnan(value):
            return value
    return float("nan")


def reduce_ablation_tables(selections: List[Dict[str, Any]], episodes: List[Dict[str, Any]], tables: Path) -> Dict[str, Any]:
    step_rows = []
    grouped = group_by(selections, "variant")
    for variant in ["wote_coarse", "utmr_fine_dt_only", "utmr_short_horizon_only", "wote_utmr"]:
        items = grouped.get(variant, [])
        if not items:
            continue
        trigger_rate = 100.0 * sum(1 for item in items if item["triggered"]) / len(items)
        selected_speed_mean, selected_speed_std = mean_std([float(item["selected_speed_kmh"]) for item in items])
        changed_rate = 100.0 * sum(1 for item in items if item["selected_index"] != item["baseline_index"]) / len(items)
        fine_coverage = 100.0 * sum(1 for item in items if item["fine_available"]) / len(items)
        step_rows.append(
            {
                "variant": VARIANTS[variant]["label"],
                "steps": len(items),
                "trigger_rate_pct": trigger_rate,
                "selection_changed_pct": changed_rate,
                "fine_score_coverage_pct": fine_coverage,
                "selected_speed_kmh_mean": selected_speed_mean,
                "selected_speed_kmh_std": selected_speed_std,
            }
        )
    step_fields = [
        "variant",
        "steps",
        "trigger_rate_pct",
        "selection_changed_pct",
        "fine_score_coverage_pct",
        "selected_speed_kmh_mean",
        "selected_speed_kmh_std",
    ]
    write_csv(tables / "table_iii_ablation_step_proxy.csv", step_rows, step_fields)
    write_markdown(tables / "table_iii_ablation_step_proxy.md", step_rows, step_fields)

    closed_loop_fields = ["variant", "episodes", "collision_pct", "success_pct", "mean_speed_kmh"]
    closed_loop_rows = []
    if episodes:
        wanted = {
            "WoTE (coarse)",
            "UTMR (fine dt only)",
            "UTMR (short horizon only)",
            "UTMR (Full)",
            "WoTE + UTMR (Ours)",
        }
        grouped_episodes = {method: items for method, items in group_by(episodes, "method").items() if method in wanted}
        for method, items in grouped_episodes.items():
            collision_mean, _ = mean_std([100.0 * float(truthy(item.get("collision", False))) for item in items])
            success_mean, _ = mean_std([100.0 * float(truthy(item.get("success", False))) for item in items])
            speed_mean, _ = mean_std([to_float(item.get("mean_speed_kmh", item.get("mean_speed"))) for item in items])
            closed_loop_rows.append(
                {
                    "variant": method,
                    "episodes": len(items),
                    "collision_pct": collision_mean,
                    "success_pct": success_mean,
                    "mean_speed_kmh": speed_mean,
                }
            )
    write_csv(tables / "table_iii_ablation_closed_loop.csv", closed_loop_rows, closed_loop_fields)
    write_markdown(tables / "table_iii_ablation_closed_loop.md", closed_loop_rows, closed_loop_fields)
    return {"step_proxy": step_rows, "closed_loop": closed_loop_rows}


def reduce_speed_uncertainty(selections: List[Dict[str, Any]], figures: Path) -> Dict[str, Any]:
    baseline = [item for item in selections if item["variant"] == "wote_coarse"]
    bins = [(0, 40), (40, 60), (60, 80), (80, 100), (100, 120), (120, 140), (140, 1_000)]
    rows = []
    for low, high in bins:
        items = [item for item in baseline if low <= float(item["ego_speed_kmh"]) < high]
        entropies = [float(item["entropy"]) for item in items]
        entropy_mean, entropy_std = mean_std(entropies)
        rows.append(
            {
                "speed_bin_kmh": f"{low}-{high if high < 1000 else 'inf'}",
                "speed_mid_kmh": (low + min(high, 160)) / 2.0,
                "steps": len(items),
                "entropy_mean": entropy_mean,
                "entropy_std": entropy_std,
            }
        )
    write_csv(figures / "fig3_speed_uncertainty.csv", rows)
    maybe_plot_speed_uncertainty(figures / "fig3_speed_uncertainty.png", rows)
    return {"bins": rows}


def reduce_selection_bias(selections: List[Dict[str, Any]], figures: Path) -> Dict[str, Any]:
    baseline = [
        item
        for item in selections
        if item["variant"] == "wote_coarse" and not math.isnan(float(item["speed_gap_kmh"]))
    ]
    rows = [
        {
            "step": item["step"],
            "episode_id": item["episode_id"],
            "entropy": item["entropy"],
            "speed_gap_kmh": item["speed_gap_kmh"],
            "baseline_speed_kmh": item["baseline_speed_kmh"],
            "oracle_speed_kmh": item["oracle_speed_kmh"],
            "feasible_count": item["feasible_count"],
        }
        for item in baseline
    ]
    slope, intercept = linear_fit(
        [float(row["entropy"]) for row in rows],
        [float(row["speed_gap_kmh"]) for row in rows],
    )
    write_csv(figures / "fig4_selection_bias.csv", rows)
    maybe_plot_selection_bias(figures / "fig4_selection_bias.png", rows, slope, intercept)
    return {"num_points": len(rows), "speed_gap_vs_entropy_slope": slope, "intercept": intercept}


def reduce_qualitative(steps: List[Dict[str, Any]], selections: List[Dict[str, Any]], figures: Path, params: UTMRParams) -> Dict[str, Any]:
    by_step = {
        (str(item.get("episode_id")), str(item.get("step"))): item
        for item in selections
        if item["variant"] == "wote_utmr"
    }
    candidates = []
    for row in steps:
        key = (str(row.get("episode_id", row.get("token"))), str(row.get("step")))
        selected = by_step.get(key)
        if selected and selected["triggered"]:
            candidates.append((float(selected["ego_speed_kmh"]), float(selected["entropy"]), row, selected))
    if not candidates:
        return {"available": False}
    _, _, row, selected = max(candidates, key=lambda item: (item[0], item[1]))
    coarse = as_float_list(get_first(row, ["coarse_scores", "final_rewards"]))
    fine_scores, fine_source = find_fine_scores(row, "wote_utmr")
    fine_scores = pad_float(fine_scores, len(coarse), float("nan"))
    feasible = feasible_mask(row, params, len(coarse))
    top = top_n_indices(coarse, feasible, min(params.top_n, len(coarse)))
    out_rows = []
    for rank, idx in enumerate(top, start=1):
        out_rows.append(
            {
                "rank_by_coarse": rank,
                "candidate_index": idx,
                "coarse_score": coarse[idx],
                "fine_score": fine_scores[idx],
                "feasible": feasible[idx],
            }
        )
    write_csv(figures / "fig5_score_landscape.csv", out_rows)
    maybe_plot_score_landscape(figures / "fig5_score_landscape.png", out_rows)
    return {
        "available": True,
        "episode_id": selected["episode_id"],
        "step": selected["step"],
        "ego_speed_kmh": selected["ego_speed_kmh"],
        "entropy": selected["entropy"],
        "margin": selected["margin"],
        "fine_source": fine_source,
    }


def group_by(rows: Iterable[Dict[str, Any]], key: str) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get(key, "")), []).append(row)
    return grouped


def percentile(values: Sequence[float], pct: float) -> float:
    values = sorted(value for value in values if not math.isnan(float(value)))
    if not values:
        return float("nan")
    if len(values) == 1:
        return float(values[0])
    pos = (len(values) - 1) * pct / 100.0
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return float(values[int(pos)])
    return float(values[low] * (high - pos) + values[high] * (pos - low))


def linear_fit(xs: Sequence[float], ys: Sequence[float]) -> Tuple[float, float]:
    points = [(x, y) for x, y in zip(xs, ys) if not math.isnan(x) and not math.isnan(y)]
    if len(points) < 2:
        return float("nan"), float("nan")
    x_mean = statistics.mean(x for x, _ in points)
    y_mean = statistics.mean(y for _, y in points)
    denom = sum((x - x_mean) ** 2 for x, _ in points)
    if denom == 0:
        return 0.0, y_mean
    slope = sum((x - x_mean) * (y - y_mean) for x, y in points) / denom
    return float(slope), float(y_mean - slope * x_mean)


def maybe_import_plot():
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        return plt
    except Exception:
        return None


def maybe_plot_speed_uncertainty(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    plt = maybe_import_plot()
    if plt is None:
        return
    xs = [float(row["speed_mid_kmh"]) for row in rows if int(row["steps"]) > 0]
    ys = [float(row["entropy_mean"]) for row in rows if int(row["steps"]) > 0]
    if not xs:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(6, 4))
    plt.plot(xs, ys, marker="o", color="#b42318")
    plt.xlabel("Speed (km/h)")
    plt.ylabel("Normalized entropy")
    plt.tight_layout()
    plt.savefig(path)
    plt.close()


def maybe_plot_selection_bias(path: Path, rows: Sequence[Dict[str, Any]], slope: float, intercept: float) -> None:
    plt = maybe_import_plot()
    if plt is None or not rows:
        return
    xs = [float(row["entropy"]) for row in rows]
    ys = [float(row["speed_gap_kmh"]) for row in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(6, 4))
    plt.scatter(xs, ys, s=10, alpha=0.45, color="#175cd3")
    if not math.isnan(slope):
        x0, x1 = min(xs), max(xs)
        plt.plot([x0, x1], [slope * x0 + intercept, slope * x1 + intercept], color="#b42318")
    plt.xlabel("Normalized entropy")
    plt.ylabel("Oracle - baseline speed (km/h)")
    plt.tight_layout()
    plt.savefig(path)
    plt.close()


def maybe_plot_score_landscape(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    plt = maybe_import_plot()
    if plt is None or not rows:
        return
    xs = [str(row["candidate_index"]) for row in rows]
    coarse = [float(row["coarse_score"]) for row in rows]
    fine = [float(row["fine_score"]) for row in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(7, 4))
    offsets = list(range(len(rows)))
    plt.bar([idx - 0.2 for idx in offsets], coarse, width=0.4, label="coarse")
    if any(not math.isnan(value) for value in fine):
        plt.bar([idx + 0.2 for idx in offsets], fine, width=0.4, label="fine")
    plt.xticks(offsets, xs)
    plt.xlabel("Candidate index")
    plt.ylabel("Score")
    plt.legend()
    plt.tight_layout()
    plt.savefig(path)
    plt.close()


def write_plan(out_dir: Path, params: UTMRParams) -> None:
    rows = [
        {
            "paper_item": "Fig.3",
            "experiment": "Speed uncertainty relationship",
            "inputs": "step JSONL: ego_speed_kmh, coarse_scores",
            "outputs": "figures/fig3_speed_uncertainty.csv,png",
        },
        {
            "paper_item": "Fig.4",
            "experiment": "Feasible-set selection bias",
            "inputs": "step JSONL: coarse_scores, feasible_mask or collision/TTC, candidate_speeds_kmh",
            "outputs": "figures/fig4_selection_bias.csv,png",
        },
        {
            "paper_item": "Table I",
            "experiment": "AWSIM closed-loop performance",
            "inputs": "episode CSV/JSONL: method, collision, success, mean_speed_kmh, driving_score",
            "outputs": "tables/table_i_main_closed_loop.csv,md",
        },
        {
            "paper_item": "Table II",
            "experiment": "NAVSIM computational efficiency",
            "inputs": "step JSONL plus optional episode PDM score",
            "outputs": "tables/table_ii_runtime.csv,md",
        },
        {
            "paper_item": "Table III",
            "experiment": "UTMR ablation",
            "inputs": "episode metrics by ablation method; optional step fine_dt/short_horizon scores",
            "outputs": "tables/table_iii_ablation_*.csv,md",
        },
        {
            "paper_item": "Fig.5",
            "experiment": "Score landscape qualitative",
            "inputs": "step JSONL: coarse_scores and fine_scores_full",
            "outputs": "figures/fig5_score_landscape.csv,png",
        },
    ]
    write_csv(out_dir / "paper_experiment_matrix.csv", rows)
    write_markdown(out_dir / "paper_experiment_matrix.md", rows, ["paper_item", "experiment", "inputs", "outputs"])
    write_json(out_dir / "params.json", asdict(params))


def smoke(args: argparse.Namespace, params: UTMRParams) -> None:
    out_dir = args.out_dir
    raw = out_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    steps_path = raw / "smoke_steps.jsonl"
    episodes_path = raw / "smoke_episodes.csv"
    make_smoke_steps(steps_path, params)
    make_smoke_episodes(episodes_path)
    steps = load_rows(steps_path)
    episodes = load_rows(episodes_path)
    reduce_experiments(steps, episodes, out_dir, params)
    print(f"smoke results: {out_dir}")


def make_smoke_steps(path: Path, params: UTMRParams) -> None:
    from utmr_core import EgoState, Obstacle, UTMRPlanner, build_paper_step_row

    cfg = runtime_config_from_params(params)
    planner = UTMRPlanner(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        for step in range(240):
            ego_speed = 55.0 + 80.0 * (step / 239.0)
            phase = (step % 80) / 80.0
            obstacles = [
                Obstacle(x_m=28.0 + 22.0 * phase, y_m=2.0 * math.sin(step / 17.0), radius_m=1.1)
            ]
            if step % 53 == 0:
                obstacles.append(Obstacle(x_m=42.0, y_m=-1.5, radius_m=1.2))
            row = build_paper_step_row(
                planner=planner,
                ego=EgoState(speed_mps=ego_speed / 3.6),
                obstacles=obstacles,
                episode_id=f"smoke_{step // 120}",
                step=step,
            )
            fp.write(json.dumps(row, separators=(",", ":")) + "\n")


def make_smoke_episodes(path: Path) -> None:
    rng = random.Random(20260708)
    specs = {
        "VAD": (0.121, 0.302, 105.2, 58.4),
        "WoTE": (0.082, 0.314, 112.4, 61.7),
        "WoTE + UTMR (Ours)": (0.069, 0.335, 134.8, 61.9),
        "WoTE + Uniform Fine": (0.064, 0.340, 136.2, 62.2),
        "UTMR (fine dt only)": (0.069, 0.321, 122.3, 61.2),
        "UTMR (short horizon only)": (0.072, 0.318, 118.7, 61.0),
        "UTMR (Full)": (0.069, 0.335, 134.8, 61.9),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fp:
        fieldnames = ["method", "episode_id", "collision", "success", "mean_speed_kmh", "driving_score", "pdm_score"]
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for method, (collision_p, success_p, speed, score) in specs.items():
            for episode in range(40):
                writer.writerow(
                    {
                        "method": method,
                        "episode_id": f"{method.replace(' ', '_')}_{episode}",
                        "collision": rng.random() < collision_p,
                        "success": rng.random() < success_p,
                        "mean_speed_kmh": speed + rng.gauss(0, 3.0),
                        "driving_score": score + rng.gauss(0, 4.0),
                        "pdm_score": 84.8
                        if method == "WoTE"
                        else 85.7
                        if "UTMR" in method
                        else 86.1
                        if "Uniform" in method
                        else "",
                    }
                )


def run_navsim_suite(args: argparse.Namespace) -> None:
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    wrapper = args.wrapper.resolve()
    if not wrapper.exists():
        raise FileNotFoundError(wrapper)

    commands = []
    for mode in ["baseline", "utmr"]:
        step_log = (out_dir / "raw" / f"navsim_{mode}_steps.jsonl").resolve()
        command = [
            "bash",
            "-lc",
            " ".join(
                [
                    f"UTMR_WOTE_STEP_LOG={shell_quote(str(step_log))}",
                    f"UTMR_WOTE_METHOD={shell_quote(mode)}",
                    f"MODE={shell_quote(mode)}",
                    shell_quote(str(wrapper)),
                    *[shell_quote(arg) for arg in args.hydra_args],
                ]
            ),
        ]
        commands.append({"mode": mode, "step_log": str(step_log), "command": command})

    write_json(out_dir / "navsim_commands.json", commands)
    if args.dry_run:
        for command in commands:
            print(" ".join(command["command"]))
        return

    for command in commands:
        Path(command["step_log"]).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(command["command"], check=True)


def shell_quote(value: str) -> str:
    if value == "":
        return "''"
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./:=+-")
    if all(ch in allowed for ch in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def main() -> None:
    args = parse_args()
    params = params_from_args(args)
    if args.command == "analyze":
        steps = load_rows(args.steps)
        episodes = load_rows(args.episodes) if args.episodes else []
        reduce_experiments(steps, episodes, args.out_dir, params)
        print(f"analysis results: {args.out_dir}")
    elif args.command == "smoke":
        smoke(args, params)
    elif args.command == "plan":
        write_plan(args.out_dir, params)
        print(f"experiment matrix: {args.out_dir}")
    elif args.command == "export-anchors":
        from utmr_core import export_anchor_array

        output = args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        export_anchor_array(
            str(output),
            speed_mps=args.anchor_speed_kmh / 3.6,
            config=runtime_config_from_params(params),
        )
        print(f"exported anchors: {output}")
    elif args.command == "run-navsim-suite":
        run_navsim_suite(args)
    else:
        raise AssertionError(args.command)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise
