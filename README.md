# UTMR Experiment Implementation

This repository records the current UTMR paper-reproduction work for NAVSIM/WoTE
and AWSIM/Autoware.

It intentionally contains only source code, scripts, scenarios, and experiment
notes. Large datasets, checkpoints, simulator binaries, Autoware builds, and raw
experiment outputs are excluded.

## What Is Included

- `experiments/utmr/`: UTMR experiment runners, analysis scripts, asset checks,
  NAVSIM/WoTE wrappers, AWSIM supervisors, scenarios, and core heuristic UTMR
  planner utilities.
- `third_party/WoTE/navsim/agents/WoTE/`: the WoTE-side patch files used for
  UTMR reranking experiments.
- `autoware/utmr_scripts/`: Autoware/AWSIM helper scripts and ROS helper nodes.
- `docs/EXPERIMENT_STATUS.md`: status, results, exact commands, and next steps.

## Current Headline Result

NAVSIM/WoTE K=64 guarded-safety UTMR on a 1000-scene subset improved over the
baseline:

| Experiment | Score | Notes |
| --- | ---: | --- |
| WoTE baseline, 1000 scenes | 0.8638675087 | 1000 success, 0 failed |
| UTMR guarded safety, 1000 scenes | 0.8720460220 | 1000 success, 0 failed, 9.5% rerank accepted |

The full 12146-scenario guarded-safety run was started after this result. See
`docs/EXPERIMENT_STATUS.md` for the running status and the post-run analysis
commands.

## Reproduction Entry Point

Read:

```bash
docs/EXPERIMENT_STATUS.md
```

The document includes expandable command blocks for the long NAVSIM runs.
