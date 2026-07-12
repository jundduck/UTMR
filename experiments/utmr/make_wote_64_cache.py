#!/usr/bin/env python3
"""Build a K=64 WoTE PDM-score cache from the released K=256 cache."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-score",
        type=Path,
        default=Path("third_party/WoTE/dataset/extra_data/planning_vb/formatted_pdm_score_256.npy"),
        help="Released WoTE score cache with 256 trajectory scores per token.",
    )
    parser.add_argument(
        "--source-anchors",
        type=Path,
        default=Path("third_party/WoTE/dataset/extra_data/planning_vb/trajectory_anchors_256.npy"),
        help="Released WoTE K=256 trajectory anchors.",
    )
    parser.add_argument(
        "--target-anchors",
        type=Path,
        default=Path("third_party/WoTE/dataset/extra_data/planning_vb/trajectory_anchors_64.npy"),
        help="K=64 trajectory anchors used by the UTMR paper experiments.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("third_party/WoTE/dataset/extra_data/planning_vb/formatted_pdm_score_64.npy"),
        help="Output K=64 score cache.",
    )
    parser.add_argument(
        "--mapping-output",
        type=Path,
        default=Path("third_party/WoTE/dataset/extra_data/planning_vb/trajectory_anchor_64_from_256_mapping.json"),
        help="JSON file that records the source anchor selected for each K=64 anchor.",
    )
    parser.add_argument(
        "--derive-target-anchors",
        action="store_true",
        help="Overwrite --target-anchors with a deterministic representative subset of --source-anchors.",
    )
    parser.add_argument(
        "--target-k",
        type=int,
        default=64,
        help="Number of anchors to select when --derive-target-anchors is used.",
    )
    return parser.parse_args()


def farthest_point_subset(source_anchors: np.ndarray, target_k: int) -> np.ndarray:
    if target_k <= 0 or target_k > source_anchors.shape[0]:
        raise ValueError(f"target_k must be in [1, {source_anchors.shape[0]}], got {target_k}")

    points = source_anchors.reshape(source_anchors.shape[0], -1).astype(np.float64)
    center = np.mean(points, axis=0)
    first = int(np.argmin(np.linalg.norm(points - center, axis=1)))
    selected = [first]
    min_distances = np.linalg.norm(points - points[first], axis=1)

    while len(selected) < target_k:
        next_index = int(np.argmax(min_distances))
        selected.append(next_index)
        next_distances = np.linalg.norm(points - points[next_index], axis=1)
        min_distances = np.minimum(min_distances, next_distances)

    return np.array(sorted(selected), dtype=np.int64)


def nearest_source_indices(source_anchors: np.ndarray, target_anchors: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if source_anchors.ndim != 3 or target_anchors.ndim != 3:
        raise ValueError(
            f"expected anchor arrays shaped (K, T, D), got {source_anchors.shape} and {target_anchors.shape}"
        )
    if source_anchors.shape[1:] != target_anchors.shape[1:]:
        raise ValueError(
            f"source and target anchors must share pose dimensions, got {source_anchors.shape} and {target_anchors.shape}"
        )

    source_flat = source_anchors.reshape(source_anchors.shape[0], -1).astype(np.float64)
    target_flat = target_anchors.reshape(target_anchors.shape[0], -1).astype(np.float64)
    distances = np.linalg.norm(target_flat[:, None, :] - source_flat[None, :, :], axis=2)
    indices = np.argmin(distances, axis=1)
    return indices.astype(np.int64), distances[np.arange(target_anchors.shape[0]), indices]


def select_k(value: Any, source_k: int, indices: np.ndarray) -> Any:
    if isinstance(value, np.ndarray):
        if value.ndim >= 1 and value.shape[0] == source_k:
            return value[indices].copy()
        return value.copy()
    if isinstance(value, dict):
        return {key: select_k(item, source_k, indices) for key, item in value.items()}
    if isinstance(value, list):
        if len(value) == source_k:
            return [copy.deepcopy(value[int(index)]) for index in indices]
        return [select_k(item, source_k, indices) for item in value]
    if isinstance(value, tuple):
        if len(value) == source_k:
            return tuple(copy.deepcopy(value[int(index)]) for index in indices)
        return tuple(select_k(item, source_k, indices) for item in value)
    return copy.deepcopy(value)


def main() -> None:
    args = parse_args()

    source_anchors = np.load(args.source_anchors)
    if args.derive_target_anchors:
        indices = farthest_point_subset(source_anchors, args.target_k)
        target_anchors = source_anchors[indices].copy()
        args.target_anchors.parent.mkdir(parents=True, exist_ok=True)
        np.save(args.target_anchors, target_anchors)
        distances = np.zeros(target_anchors.shape[0], dtype=np.float64)
    else:
        target_anchors = np.load(args.target_anchors)
        indices, distances = nearest_source_indices(source_anchors, target_anchors)

    source_scores = np.load(args.source_score, allow_pickle=True).item()
    if not isinstance(source_scores, dict):
        raise TypeError(f"expected score cache to contain a dict, got {type(source_scores).__name__}")

    converted = select_k(source_scores, source_anchors.shape[0], indices)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.save(args.output, converted)

    mapping = {
        "source_score": str(args.source_score),
        "source_anchors": str(args.source_anchors),
        "target_anchors": str(args.target_anchors),
        "output": str(args.output),
        "source_k": int(source_anchors.shape[0]),
        "target_k": int(target_anchors.shape[0]),
        "source_indices": [int(index) for index in indices],
        "unique_source_count": int(len(set(int(index) for index in indices))),
        "distance_mean": float(np.mean(distances)),
        "distance_max": float(np.max(distances)),
        "derived_target_anchors": bool(args.derive_target_anchors),
    }
    args.mapping_output.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")

    if args.derive_target_anchors:
        print(f"saved {args.target_anchors}")
    print(f"saved {args.output}")
    print(f"saved {args.mapping_output}")
    print(
        "mapped "
        f"{mapping['target_k']} target anchors to {mapping['unique_source_count']} unique source anchors "
        f"(mean distance {mapping['distance_mean']:.3f}, max {mapping['distance_max']:.3f})"
    )


if __name__ == "__main__":
    main()
