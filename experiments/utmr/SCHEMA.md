# UTMR paper experiment log schema

The reducer accepts real AWSIM/NAVSIM logs in two files.

Step-level JSONL, one planning step per line:

```json
{
  "episode_id": "route_001",
  "step": 42,
  "method_variant": "utmr",
  "latency_ms": 22.6,
  "ego_speed_kmh": 118.3,
  "coarse_scores": [0.1, 0.2],
  "fine_scores_full": [0.15, 0.33],
  "fine_dt_scores": [0.14, 0.28],
  "short_horizon_scores": [0.12, 0.25],
  "candidate_speeds_kmh": [95.0, 132.0],
  "feasible_mask": [true, true],
  "ttc_s": [4.1, 1.6]
}
```

Required for every step: `coarse_scores`.

Recommended for Fig.3/Fig.4/Fig.5 and ablations:

- `method_variant` and `latency_ms` for Table II
- `ego_speed_kmh`
- `candidate_speeds_kmh`
- either `feasible_mask`, or `collision_mask` plus `ttc_s`
- `fine_scores_full` for UTMR full and Uniform Fine
- `fine_dt_scores` for the fine-dt-only ablation
- `short_horizon_scores` for the short-horizon-only ablation

Episode-level CSV or JSONL:

```csv
method,episode_id,collision,success,mean_speed_kmh,driving_score,pdm_score
WoTE,route_001,false,true,112.4,61.7,
WoTE + UTMR (Ours),route_001,false,true,134.8,61.9,
```

AWSIM scenario files are JSON:

```json
{
  "scenarios": [
    {
      "scenario_id": "shinjuku_route_001",
      "route_length_m": 20.25,
      "initial_pose": {"x": 81377.0, "y": 49917.0, "z": 41.3, "yaw_rad": 0.5895},
      "goal_pose": {"x": 81393.98, "y": 49928.02, "z": 41.27, "yaw_rad": 0.5895},
      "obstacle_frame": "ego",
      "obstacles": []
    }
  ]
}
```

The reducer writes:

- `tables/table_i_main_closed_loop.*`
- `tables/table_ii_runtime.*`
- `tables/table_iii_ablation_*`
- `figures/fig3_speed_uncertainty.*`
- `figures/fig4_selection_bias.*`
- `figures/fig5_score_landscape.*`
