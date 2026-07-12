from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np


@dataclass(frozen=True)
class UTMRRuntimeConfig:
    k: int = 64
    speed_bins: int = 8
    curvature_bins: int = 8
    speed_delta_low_mps: float = -12.0
    speed_delta_high_mps: float = 12.0
    min_target_speed_mps: float = 1.0
    curvature_min: float = -0.0025
    curvature_max: float = 0.0025
    anchor_horizon_s: float = 4.0
    anchor_dt_s: float = 0.5
    coarse_horizon_s: float = 2.0
    coarse_dt_s: float = 0.20
    fine_horizon_s: float = 1.0
    fine_dt_s: float = 0.05
    beta: float = 1.0
    gamma_h: float = 0.75
    gamma_m: float = 0.05
    top_n: int = 8
    ttc_threshold_s: float = 1.0
    lane_half_width_m: float = 8.0
    ego_radius_m: float = 1.2

    def __post_init__(self) -> None:
        if self.speed_bins * self.curvature_bins != self.k:
            raise ValueError("speed_bins * curvature_bins must equal k")


@dataclass(frozen=True)
class EgoState:
    speed_mps: float
    acceleration_mps2: float = 0.0


@dataclass(frozen=True)
class Obstacle:
    x_m: float
    y_m: float
    radius_m: float = 1.0
    label: str = "obstacle"


@dataclass
class CandidateBatch:
    initial_speed_mps: float
    target_speeds_mps: np.ndarray
    curvatures: np.ndarray

    @property
    def size(self) -> int:
        return int(self.target_speeds_mps.shape[0])

    def sample(self, horizon_s: float, dt_s: float) -> np.ndarray:
        times = np.arange(dt_s, horizon_s + 1e-9, dt_s, dtype=np.float64)
        trajectories = np.zeros((self.size, len(times), 3), dtype=np.float32)
        for idx, (target_speed, curvature) in enumerate(zip(self.target_speeds_mps, self.curvatures)):
            trajectories[idx] = sample_cubic_spline_primitive(
                float(self.initial_speed_mps),
                float(target_speed),
                float(curvature),
                times,
                horizon_s,
            )
        return trajectories

    def anchor_poses(self, horizon_s: float = 4.0, dt_s: float = 0.5) -> np.ndarray:
        return self.sample(horizon_s, dt_s)


@dataclass
class ScoreBundle:
    scores: np.ndarray
    feasible_mask: np.ndarray
    collision_mask: np.ndarray
    ttc_s: np.ndarray
    metrics: Dict[str, np.ndarray]
    trajectories: np.ndarray


@dataclass
class SelectionResult:
    mode: str
    selected_index: int
    baseline_index: int
    triggered: bool
    entropy: float
    margin: float
    top_indices: List[int]
    coarse: ScoreBundle
    fine: Optional[ScoreBundle]
    candidates: CandidateBatch

    def selected_trajectory(self) -> np.ndarray:
        source = self.fine if self.fine is not None else self.coarse
        return source.trajectories[self.selected_index]

    def to_step_log(self, episode_id: str = "", step: int = 0, latency_ms: Optional[float] = None) -> Dict[str, object]:
        row: Dict[str, object] = {
            "episode_id": episode_id,
            "step": step,
            "method_variant": self.mode,
            "ego_speed_kmh": float(self.candidates.initial_speed_mps * 3.6),
            "coarse_scores": self.coarse.scores.tolist(),
            "candidate_speeds_kmh": (self.candidates.target_speeds_mps * 3.6).tolist(),
            "candidate_curvatures": self.candidates.curvatures.tolist(),
            "feasible_mask": self.coarse.feasible_mask.astype(bool).tolist(),
            "collision_mask": self.coarse.collision_mask.astype(bool).tolist(),
            "ttc_s": self.coarse.ttc_s.tolist(),
            "entropy": self.entropy,
            "margin": self.margin,
            "triggered": self.triggered,
            "baseline_index": self.baseline_index,
            "selected_index": self.selected_index,
            "top_indices": self.top_indices,
        }
        if latency_ms is not None:
            row["latency_ms"] = float(latency_ms)
        if self.fine is not None:
            key = {
                "utmr": "fine_scores_full",
                "uniform_fine": "fine_scores_full",
                "fine_dt_only": "fine_dt_scores",
                "short_horizon_only": "short_horizon_scores",
            }.get(self.mode, "fine_scores")
            row[key] = self.fine.scores.tolist()
        return row


class CubicSplineCandidateGenerator:
    def __init__(self, config: UTMRRuntimeConfig):
        self.config = config

    def generate(self, ego: EgoState) -> CandidateBatch:
        low = max(self.config.min_target_speed_mps, ego.speed_mps + self.config.speed_delta_low_mps)
        high = max(low + 0.1, ego.speed_mps + self.config.speed_delta_high_mps)
        speed_levels = np.linspace(low, high, self.config.speed_bins, dtype=np.float64)
        curvature_levels = np.linspace(
            self.config.curvature_min,
            self.config.curvature_max,
            self.config.curvature_bins,
            dtype=np.float64,
        )
        target_speeds = []
        curvatures = []
        for speed in speed_levels:
            for curvature in curvature_levels:
                target_speeds.append(speed)
                curvatures.append(curvature)
        return CandidateBatch(
            initial_speed_mps=float(ego.speed_mps),
            target_speeds_mps=np.asarray(target_speeds, dtype=np.float64),
            curvatures=np.asarray(curvatures, dtype=np.float64),
        )


class HeuristicTrajectoryScorer:
    def __init__(self, config: UTMRRuntimeConfig):
        self.config = config

    def score(
        self,
        candidates: CandidateBatch,
        ego: EgoState,
        horizon_s: float,
        dt_s: float,
        obstacles: Sequence[Obstacle] = (),
    ) -> ScoreBundle:
        trajectories = candidates.sample(horizon_s, dt_s)
        target_speeds = candidates.target_speeds_mps
        curvatures = candidates.curvatures

        x_end = trajectories[:, -1, 0].astype(np.float64)
        max_progress = max(float(np.max(x_end)), 1.0)
        progress = np.clip(x_end / max_progress, 0.0, 1.0)

        desired_speed = max(ego.speed_mps + 4.0, 1.0)
        speed_error = np.abs(target_speeds - desired_speed)
        speed_score = np.clip(1.0 - speed_error / max(desired_speed, 8.0), 0.0, 1.0)

        max_abs_y = np.max(np.abs(trajectories[:, :, 1]), axis=1).astype(np.float64)
        drivable = np.clip(1.0 - np.maximum(0.0, max_abs_y - self.config.lane_half_width_m) / self.config.lane_half_width_m, 0.0, 1.0)

        max_curvature = max(abs(self.config.curvature_min), abs(self.config.curvature_max), 1e-6)
        comfort = np.clip(1.0 - np.abs(curvatures) / max_curvature, 0.0, 1.0)

        collision_mask, ttc_s = self._collision_and_ttc(trajectories, dt_s, obstacles)
        no_collision = 1.0 - collision_mask.astype(np.float64)
        ttc_score = np.clip(ttc_s / max(self.config.ttc_threshold_s, 1e-6), 0.0, 1.0)

        scores = (
            0.35 * progress
            + 0.25 * speed_score
            + 0.18 * drivable
            + 0.12 * comfort
            + 0.07 * no_collision
            + 0.03 * ttc_score
        )
        feasible_mask = (
            (collision_mask == 0)
            & (ttc_s >= self.config.ttc_threshold_s)
            & (max_abs_y <= self.config.lane_half_width_m)
        )

        return ScoreBundle(
            scores=scores.astype(np.float64),
            feasible_mask=feasible_mask.astype(bool),
            collision_mask=collision_mask.astype(bool),
            ttc_s=ttc_s.astype(np.float64),
            metrics={
                "progress": progress.astype(np.float64),
                "speed_score": speed_score.astype(np.float64),
                "drivable": drivable.astype(np.float64),
                "comfort": comfort.astype(np.float64),
                "no_collision": no_collision.astype(np.float64),
                "ttc_score": ttc_score.astype(np.float64),
            },
            trajectories=trajectories,
        )

    def _collision_and_ttc(
        self,
        trajectories: np.ndarray,
        dt_s: float,
        obstacles: Sequence[Obstacle],
    ) -> Tuple[np.ndarray, np.ndarray]:
        num_candidates = trajectories.shape[0]
        collision = np.zeros(num_candidates, dtype=bool)
        ttc = np.full(num_candidates, trajectories.shape[1] * dt_s + dt_s, dtype=np.float64)
        if not obstacles:
            return collision, ttc

        xy = trajectories[:, :, :2].astype(np.float64)
        times = np.arange(1, trajectories.shape[1] + 1, dtype=np.float64) * dt_s
        for obstacle in obstacles:
            center = np.asarray([obstacle.x_m, obstacle.y_m], dtype=np.float64)
            threshold = self.config.ego_radius_m + obstacle.radius_m
            distances = np.linalg.norm(xy - center[None, None, :], axis=2)
            hits = distances <= threshold
            candidate_hits = np.any(hits, axis=1)
            collision |= candidate_hits
            for idx in np.where(candidate_hits)[0]:
                first_hit = int(np.argmax(hits[idx]))
                ttc[idx] = min(ttc[idx], float(times[first_hit]))
        return collision, ttc


class UTMRPlanner:
    def __init__(
        self,
        config: Optional[UTMRRuntimeConfig] = None,
        generator: Optional[CubicSplineCandidateGenerator] = None,
        scorer: Optional[HeuristicTrajectoryScorer] = None,
    ):
        self.config = config or UTMRRuntimeConfig()
        self.generator = generator or CubicSplineCandidateGenerator(self.config)
        self.scorer = scorer or HeuristicTrajectoryScorer(self.config)

    def plan(
        self,
        ego: EgoState,
        mode: str = "utmr",
        obstacles: Sequence[Obstacle] = (),
    ) -> SelectionResult:
        candidates = self.generator.generate(ego)
        coarse = self.scorer.score(
            candidates,
            ego,
            self.config.coarse_horizon_s,
            self.config.coarse_dt_s,
            obstacles,
        )
        entropy, margin = entropy_and_margin(coarse.scores, self.config.beta)
        baseline_idx = masked_argmax(coarse.scores, coarse.feasible_mask)
        uncertainty_triggered = entropy > self.config.gamma_h or margin < self.config.gamma_m

        selected_idx = baseline_idx
        top_indices: List[int] = []
        fine: Optional[ScoreBundle] = None
        should_rerank = False

        if mode == "coarse":
            triggered = False
        elif mode == "utmr":
            triggered = uncertainty_triggered
            should_rerank = triggered
        elif mode == "uniform_fine":
            triggered = True
            should_rerank = True
        elif mode == "fine_dt_only":
            triggered = uncertainty_triggered
            should_rerank = triggered
        elif mode == "short_horizon_only":
            triggered = uncertainty_triggered
            should_rerank = triggered
        else:
            raise ValueError(f"unknown UTMR mode: {mode}")

        if should_rerank:
            horizon_s, dt_s = self._fine_schedule(mode)
            fine = self.scorer.score(candidates, ego, horizon_s, dt_s, obstacles)
            top_indices = top_n_indices(coarse.scores, coarse.feasible_mask, self.config.top_n)
            if top_indices:
                selected_idx = max(top_indices, key=lambda idx: fine.scores[idx])

        return SelectionResult(
            mode=mode,
            selected_index=int(selected_idx),
            baseline_index=int(baseline_idx),
            triggered=bool(triggered),
            entropy=float(entropy),
            margin=float(margin),
            top_indices=top_indices,
            coarse=coarse,
            fine=fine,
            candidates=candidates,
        )

    def score_all_schedules(
        self,
        ego: EgoState,
        obstacles: Sequence[Obstacle] = (),
    ) -> Tuple[CandidateBatch, Dict[str, ScoreBundle]]:
        candidates = self.generator.generate(ego)
        schedules = {
            "coarse": (self.config.coarse_horizon_s, self.config.coarse_dt_s),
            "fine_scores_full": (self.config.fine_horizon_s, self.config.fine_dt_s),
            "fine_dt_scores": (self.config.coarse_horizon_s, self.config.fine_dt_s),
            "short_horizon_scores": (self.config.fine_horizon_s, self.config.coarse_dt_s),
        }
        return candidates, {
            key: self.scorer.score(candidates, ego, horizon, dt, obstacles)
            for key, (horizon, dt) in schedules.items()
        }

    def _fine_schedule(self, mode: str) -> Tuple[float, float]:
        if mode == "fine_dt_only":
            return self.config.coarse_horizon_s, self.config.fine_dt_s
        if mode == "short_horizon_only":
            return self.config.fine_horizon_s, self.config.coarse_dt_s
        return self.config.fine_horizon_s, self.config.fine_dt_s


def sample_cubic_spline_primitive(
    initial_speed_mps: float,
    target_speed_mps: float,
    curvature: float,
    times: np.ndarray,
    horizon_s: float,
) -> np.ndarray:
    acceleration = (target_speed_mps - initial_speed_mps) / max(horizon_s, 1e-6)
    distances = initial_speed_mps * times + 0.5 * acceleration * times * times
    distances = np.maximum.accumulate(np.maximum(distances, 0.0))
    length = max(float(distances[-1]), 1e-3)

    end_y = 0.25 * curvature * length * length
    end_yaw = float(np.clip(curvature * length, -0.7, 0.7))
    end_slope = math.tan(end_yaw)
    a = (length * end_slope - 2.0 * end_y) / (length ** 3)
    b = (3.0 * end_y - length * end_slope) / (length ** 2)

    x = distances
    y = a * x ** 3 + b * x ** 2
    dydx = 3.0 * a * x ** 2 + 2.0 * b * x
    yaw = np.arctan(dydx)
    return np.stack([x, y, yaw], axis=1).astype(np.float32)


def entropy_and_margin(scores: Sequence[float], beta: float = 1.0) -> Tuple[float, float]:
    values = np.asarray(scores, dtype=np.float64)
    if values.size == 0:
        return float("nan"), float("nan")
    span = float(values.max() - values.min())
    if span < 1e-12:
        normalized = np.zeros_like(values)
    else:
        normalized = (values - values.min()) / span
    logits = normalized / max(beta, 1e-6)
    logits = logits - logits.max()
    probabilities = np.exp(logits)
    probabilities = probabilities / probabilities.sum()
    entropy = -float(np.sum(probabilities * np.log(np.clip(probabilities, 1e-12, None)))) / math.log(values.size)
    top2 = np.sort(normalized)[-2:]
    margin = float(top2[-1] - top2[-2]) if values.size >= 2 else float("inf")
    return entropy, margin


def masked_argmax(scores: Sequence[float], feasible_mask: Sequence[bool]) -> int:
    values = np.asarray(scores, dtype=np.float64)
    feasible = np.asarray(feasible_mask, dtype=bool)
    safe_values = np.where(feasible, values, -np.inf)
    if np.all(~np.isfinite(safe_values)):
        return int(np.argmax(values))
    return int(np.argmax(safe_values))


def top_n_indices(scores: Sequence[float], feasible_mask: Sequence[bool], n: int) -> List[int]:
    values = np.asarray(scores, dtype=np.float64)
    feasible = np.asarray(feasible_mask, dtype=bool)
    indices = [int(idx) for idx in np.where(feasible)[0]]
    indices.sort(key=lambda idx: float(values[idx]), reverse=True)
    return indices[:n]


def build_paper_step_row(
    planner: UTMRPlanner,
    ego: EgoState,
    obstacles: Sequence[Obstacle],
    episode_id: str,
    step: int,
) -> Dict[str, object]:
    candidates, bundles = planner.score_all_schedules(ego, obstacles)
    coarse = bundles["coarse"]
    entropy, margin = entropy_and_margin(coarse.scores, planner.config.beta)
    baseline_idx = masked_argmax(coarse.scores, coarse.feasible_mask)
    triggered = entropy > planner.config.gamma_h or margin < planner.config.gamma_m
    return {
        "episode_id": episode_id,
        "step": step,
        "ego_speed_kmh": float(ego.speed_mps * 3.6),
        "coarse_scores": coarse.scores.tolist(),
        "fine_scores_full": bundles["fine_scores_full"].scores.tolist(),
        "fine_dt_scores": bundles["fine_dt_scores"].scores.tolist(),
        "short_horizon_scores": bundles["short_horizon_scores"].scores.tolist(),
        "candidate_speeds_kmh": (candidates.target_speeds_mps * 3.6).tolist(),
        "candidate_curvatures": candidates.curvatures.tolist(),
        "feasible_mask": coarse.feasible_mask.astype(bool).tolist(),
        "collision_mask": coarse.collision_mask.astype(bool).tolist(),
        "ttc_s": coarse.ttc_s.tolist(),
        "entropy": float(entropy),
        "margin": float(margin),
        "triggered": bool(triggered),
        "baseline_index": int(baseline_idx),
    }


def export_anchor_array(output_path: str, speed_mps: float = 30.0, config: Optional[UTMRRuntimeConfig] = None) -> None:
    cfg = config or UTMRRuntimeConfig()
    generator = CubicSplineCandidateGenerator(cfg)
    batch = generator.generate(EgoState(speed_mps=speed_mps))
    anchors = batch.anchor_poses(cfg.anchor_horizon_s, cfg.anchor_dt_s)
    np.save(output_path, anchors)
