# UTMR experiment notes

This directory keeps the paper-reproduction work inside `/home/yax/UTMR` without symlinks.

Paper settings from the attachment:

- candidate trajectories: `K=64`
- coarse scoring: `Tc=2.0`, `dtc=0.20`
- uncertainty trigger: normalized score entropy `H > 0.75` or top-2 margin `m < 0.05`
- triggered rerank: top `N=8`, `Tf=1.0`, `dtf=0.05`
- safety filter: coarse long-horizon collision/TTC hard filter before reranking
- benchmark: AWSIM high-speed closed loop, 200 episodes per method, 120 s timeout

Local status:

- `third_party/WoTE` contains the public WoTE/NAVSIM code with UTMR trigger diagnostics added.
- The public WoTE repository does not include the paper's AWSIM high-speed benchmark scenarios or a true exposed multi-rate rollout API for `Tf=1.0`, `dtf=0.05`.
- `utmr_core.py` implements K=64 cubic-spline primitive generation, coarse/fine scoring schedules, coarse feasibility filtering, UTMR, Uniform Fine, and the two ablations.
- `closed_loop_runner.py` runs a local high-speed closed-loop benchmark with the same logging schema used by the paper reducers.
- `autoware/utmr_scripts/helpers/utmr_planner_node.py` publishes UTMR-selected trajectories to `/planning/trajectory`.
- `awsim_supervisor.py` can start AWSIM, Autoware, and the UTMR planner process with per-session logs and scenario-file route setup.
- `awsim_batch_runner.py` runs baseline/UTMR/ablation variants across repeated AWSIM scenarios and merges logs for the paper reducers.
- `paper_experiments.py` implements the paper reducers for Fig.3, Fig.4, Table I, Table II, Table III, and Fig.5.
- The current WoTE integration can log step-level coarse scores and UTMR diagnostics on NAVSIM once the required WoTE/NAVSIM assets are placed under `third_party/WoTE`.

Useful commands:

```bash
cd /home/yax/UTMR
chmod +x experiments/utmr/*.sh
experiments/utmr/setup_wote_runtime.sh
experiments/utmr/check_assets.sh
experiments/utmr/prepare_wote_assets.sh
experiments/utmr/prepare_navsim_data.sh

experiments/utmr/run_paper_experiments.sh plan --out-dir experiments/utmr/results/plan
experiments/utmr/run_paper_experiments.sh smoke --out-dir experiments/utmr/results/smoke

experiments/utmr/run_closed_loop_experiment.sh \
  --out-dir experiments/utmr/results/closed_loop \
  --episodes 20 \
  --timeout-s 120 \
  --analyze

experiments/utmr/run_closed_loop_experiment.sh \
  --out-dir experiments/utmr/results/sample_scenarios \
  --scenario-file experiments/utmr/scenarios/high_speed_sample.json \
  --timeout-s 120 \
  --analyze

experiments/utmr/run_paper_experiments.sh analyze \
  --steps path/to/steps.jsonl \
  --episodes path/to/episodes.csv \
  --out-dir experiments/utmr/results/real

experiments/utmr/run_paper_experiments.sh run-navsim-suite \
  --out-dir experiments/utmr/results/navsim \
  --dry-run

UTMR_MODE=utmr UTMR_STEP_LOG=experiments/utmr/results/awsim_live/raw/utmr_steps.jsonl \
  autoware/utmr_scripts/run_utmr_demo.sh

UTMR_MODE=utmr \
UTMR_OBJECTS_TOPIC=/perception/object_recognition/objects \
UTMR_OBJECTS_MSG_TYPE=PredictedObjects \
UTMR_STEP_LOG=experiments/utmr/results/awsim_live/raw/utmr_steps.jsonl \
UTMR_EPISODE_CSV=experiments/utmr/results/awsim_live/raw/awsim_episodes.csv \
  autoware/utmr_scripts/run_utmr_demo.sh

experiments/utmr/run_awsim_supervisor.sh \
  --out-dir experiments/utmr/results/awsim_session \
  --scenario-file experiments/utmr/scenarios/awsim_shinjuku_sample.json \
  --variant utmr \
  --timeout-s 120

autoware/utmr_scripts/probe_live_topics.sh  # prints suggested UTMR_OBJECTS_* / UTMR_COLLISION_TOPIC exports

experiments/utmr/run_awsim_batch.sh \
  --out-dir experiments/utmr/results/awsim_batch \
  --scenario-file experiments/utmr/scenarios/awsim_shinjuku_sample.json \
  --variants baseline utmr uniform_fine fine_dt_only short_horizon_only \
  --episodes 5 \
  --timeout-s 120
```

`setup_wote_runtime.sh` installs the UTMR-local Python packages needed to import WoTE/NAVSIM without creating a virtualenv or symlinks. `source_wote_runtime.sh` exports `PYTHONPATH`, `NAVSIM_DEVKIT_ROOT`, `OPENSCENE_DATA_ROOT`, and the matching map/exp roots for the current shell.

`prepare_wote_assets.sh` clones or updates `liyingyanUCAS/WoTE`, downloads the released WoTE checkpoint, ResNet-34 backbone, K=256 anchors, K=256 PDM-score cache, then derives matching K=64 anchors and a K=64 PDM-score cache from the released K=256 files.

`prepare_navsim_data.sh` prepares NAVSIM paths in the WoTE tree without symlinks. With no flags it downloads or verifies nuPlan maps and OpenScene test metadata. Test sensor blobs are much larger, so they are only downloaded when `--include-test-sensors` is passed. Download archives are deleted after successful extraction unless `--keep-archives` is passed. Metric cache generation is available with `--metric-cache` after maps and metadata are present.

```bash
experiments/utmr/prepare_navsim_data.sh
experiments/utmr/prepare_navsim_data.sh --include-test-sensors --metric-cache
experiments/utmr/prepare_navsim_data.sh --include-test-sensors --sensor-start 0 --sensor-end 0
NAVSIM_EXP_ROOT=$PWD/experiments/utmr/results/navsim_metric_smoke \
  experiments/utmr/prepare_navsim_data.sh --no-maps --no-test-metadata --metric-cache --metric-cache-max-scenes 1
```

If only part of the test sensor set has been downloaded, `run_navsim_received_subset.sh` finds navtest logs whose camera/lidar blobs are already complete, builds a metric cache for that subset, then runs the requested WoTE modes:

```bash
experiments/utmr/run_navsim_received_subset.sh \
  --out-dir experiments/utmr/results/navsim_received_subset \
  --max-scenes 1 \
  --modes baseline utmr
```

For paper-compatible `K=64`, run:

```bash
source experiments/utmr/source_wote_runtime.sh
experiments/utmr/make_wote_64_cache.py --derive-target-anchors

NUM_TRAJ_ANCHOR=64 MODE=utmr UTMR_WOTE_STEP_LOG=experiments/utmr/results/navsim/raw/utmr_steps.jsonl \
  experiments/utmr/run_navsim_wote_eval.sh
```

See `SCHEMA.md` for the real step/episode log fields expected by the paper reducers.

Useful live-topic environment variables:

- `UTMR_OBJECTS_TOPIC`: object topic for obstacle-aware scoring
- `UTMR_OBJECTS_MSG_TYPE`: `PredictedObjects`, `TrackedObjects`, or `DetectedObjects`
- `UTMR_COLLISION_TOPIC`: optional `std_msgs/Bool` collision topic for episode metrics
- `UTMR_COLLISION_OUTPUT_TOPIC`: default `/utmr/collision` bridge output when no simulator collision topic exists
- `UTMR_STATIC_OBSTACLE_FRAME`: `ego` or `map` for obstacles injected through scenario JSON
- `UTMR_GOAL_X`, `UTMR_GOAL_Y`, `UTMR_GOAL_RADIUS_M`: optional goal-arrival metric fallback
- `UTMR_ROUTE_LENGTH_M`: optional progress/driving-score normalization
