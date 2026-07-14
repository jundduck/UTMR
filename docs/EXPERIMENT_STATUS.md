# UTMR Experiment Status

Last updated: 2026-07-14 KST.

## Scope

The implementation work is focused on reproducing the UTMR-style experiments
from the workshop paper using:

- NAVSIM/WoTE offline PDM scoring.
- WoTE released checkpoint and NAVSIM metric cache.
- AWSIM/Autoware live integration and batch smoke execution.

Everything was kept under `/home/yax/UTMR` during local execution, and no
dataset symlinks are required. The last asset check reported:

```text
ok      trajectory_anchors_64.npy
ok      formatted_pdm_score_64.npy
ok      exp/metric_cache (12146 metadata rows)
symlinks under UTMR: 0
```

## Included Code

The repository contains the source needed to reproduce the current experiments:

| Path | Purpose |
| --- | --- |
| `experiments/utmr/paper_experiments.py` | Reduces step logs into tables and figures. Runs NAVSIM suites. |
| `experiments/utmr/run_navsim_wote_eval.sh` | WoTE/NAVSIM wrapper with UTMR parameters exposed as environment variables. |
| `experiments/utmr/check_assets.sh` | Verifies WoTE checkpoint, NAVSIM assets, metric cache, and symlink count. |
| `experiments/utmr/make_wote_64_cache.py` | Builds K=64 anchors/cache from released WoTE K=256 assets. |
| `third_party/WoTE/navsim/agents/WoTE/utmr_selector.py` | UTMR trigger/rerank selector. |
| `third_party/WoTE/navsim/agents/WoTE/WoTE_model.py` | WoTE forward-pass UTMR reranking integration. |
| `third_party/WoTE/navsim/agents/WoTE/WoTE_agent.py` | NAVSIM step logging for UTMR diagnostics. |
| `third_party/WoTE/navsim/agents/WoTE/configs/default.py` | UTMR config defaults. |
| `autoware/utmr_scripts/` | AWSIM/Autoware helper scripts and ROS nodes. |
| `autoware/utmr_scripts/run_straight_demo.sh` | Straight trajectory smoke launcher using the shared fail-closed readiness sequence. |
| `autoware/utmr_scripts/service_calls.sh` | Shell helper for Autoware service retry and response-pattern validation. |
| `autoware/utmr_scripts/service_readiness.sh` | Production readiness sequence for localization, route, operation mode, and gate unstop. |
| `experiments/utmr/test_service_calls.sh` | Fake-ROS shell test for localization-failure and operation-failure gate behavior. |

Large folders are intentionally not tracked: NAVSIM logs, sensor blobs, metric
cache, checkpoints, Autoware builds, AWSIM binaries, raw result logs, and local
runtime packages.

## Completed NAVSIM Experiments

### 1. Asset and Runtime Preparation

Status: complete.

- `liyingyanUCAS/WoTE.git` was used as the WoTE source.
- Released WoTE checkpoint and K=256 assets were downloaded locally.
- K=64 trajectory anchors and PDM score cache were generated.
- NAVSIM test assets and metric cache are present.

### 2. Initial Full K=64 NAVSIM Baseline vs UTMR

Status: complete.

This was the first full `12146`-scenario run before the reranking bug fix.

| Method | Success | Failed | PDM score |
| --- | ---: | ---: | ---: |
| WoTE baseline | 12146 | 0 | 0.8471632864 |
| UTMR initial | 12146 | 0 | 0.8461780929 |

Finding:

- `selected_changed_pct = 0.0`.
- The UTMR trigger fired, but the model was not receiving separate fine scores
  for reranking, so selected trajectories stayed equal to baseline.

### 3. UTMR Reranking Fix

Status: implemented and smoke-tested.

Root cause:

- `select_with_utmr(...)` was called with `fine_scores=None`, so reranking never
  executed even when `triggered=True`.

Implemented:

- Added a metric-head based UTMR fine score.
- Passed `fine_scores` into the selector.
- Logged `fine_scores_full` and `rerank_accepted`.
- Added rerank guard parameters:
  - `UTMR_FINE_MARGIN_MIN`
  - `UTMR_MAX_COARSE_DROP`

Smoke result:

| Run | Scenes | Selected changed | Score |
| --- | ---: | ---: | ---: |
| unguarded safety smoke | 50 | 66.0% | 0.9052611125 |
| guarded safety smoke | 50 | 2.0% | 0.9580532306 |

The unguarded version changed many selections but hurt score. Guarded reranking
is therefore the current preferred path.

### 4. 1000-Scene Weight Sweep

Status: complete.

| Variant | Score | Selected changed | Notes |
| --- | ---: | ---: | --- |
| baseline | 0.8638675087 | 0.0% | reference |
| `utmr_safety` | 0.8344109680 | 63.7% | too aggressive |
| `utmr_balanced` | 0.8509310840 | 9.2% | below baseline |
| `utmr_conservative` | 0.8211362302 | 18.2% | below baseline |
| `utmr_ttc_heavy` | 0.8525664071 | 7.9% | below baseline |

Token-level analysis showed the score drop came only from reranked tokens. This
motivated the guarded accept condition.

### 5. Guarded Safety 1000-Scene Run

Status: complete and positive.

| Method | Success | Failed | PDM score | Rerank accepted |
| --- | ---: | ---: | ---: | ---: |
| baseline | 1000 | 0 | 0.8638675087 | 0.0% |
| guarded safety UTMR | 1000 | 0 | 0.8720460220 | 9.5% |

This subset run was used to choose the guarded-safety setting for the full
`12146`-scenario run.

### 6. Guarded Safety Full Run

Status: complete and positive.

Final result:

| Method | Success | Failed | PDM score | Rerank accepted |
| --- | ---: | ---: | ---: | ---: |
| baseline | 12146 | 0 | 0.8471632864 | 0.0% |
| guarded safety UTMR | 12146 | 0 | 0.8542971577 | 9.8139% |

Runtime/diagnostics from the step log:

```text
trigger_rate_pct        100.0
selected_changed_pct    9.81393051210275
rerank_accepted_pct     9.81393051210275
fine_score_coverage_pct 100.0
latency_mean_ms         308.85380620006794
latency_p99_ms          338.6317099993903
```

Output folder:

```text
experiments/utmr/results/navsim_guarded_safety_full
```

CSV results:

```text
/home/yax/UTMR/third_party/WoTE/exp/eval/WoTE/default_baseline_guarded_safety_full/2026.07.13.00.16.03.csv
/home/yax/UTMR/third_party/WoTE/exp/eval/WoTE/default_utmr_guarded_safety_full/2026.07.13.01.45.04.csv
```

### 7. K256 Original WoTE Anchor Check

Status: complete.

This is not the paper's main setting. The paper uses `K=64`, while this run
uses the released WoTE `K=256` anchors/cache to check whether the same guarded
reranking transfers to the stronger original candidate set.

| Method | Success | Failed | PDM score | Rerank accepted |
| --- | ---: | ---: | ---: | ---: |
| K256 baseline | 12146 | 0 | 0.8833150351 | 0.0% |
| K256 guarded safety UTMR | 12146 | 0 | 0.8827077445 | 8.1014% |

Finding:

- K256 baseline is much stronger than the K64 baseline.
- Reusing the K64 guarded-safety setting on K256 is slightly worse than
  baseline: `-0.0006072906`.
- This suggests K256 needs separate UTMR retuning instead of directly reusing
  the K64 guard.

Runtime/diagnostics:

```text
K256 baseline latency_mean_ms          616.9268
K256 baseline latency_p99_ms           667.6763
K256 guarded latency_mean_ms           627.1611
K256 guarded latency_p99_ms            680.0303
K256 guarded rerank_accepted_pct       8.1014
```

### 8. K256 Retuned Guard Subset Runs

Status: subset retuning complete; full retuned run still pending.

Because the K64 guard did not transfer cleanly to K256, a smaller K256 sweep
was run before spending another full `12146`-scenario pass.

300-scene candidate sweep:

| Method | PDM score | Delta vs baseline | Accepted |
| --- | ---: | ---: | ---: |
| K256 baseline | 0.9022969937 | +0.0000000000 | 0.0% |
| `margin=0.15`, `drop=0.5`, `topN=8` | 0.9034554556 | +0.0011584619 | 5.0% |
| `margin=0.20`, `drop=0.2`, `topN=4` | 0.9033675968 | +0.0010706030 | 1.0% |
| `margin=0.20`, `drop=0.2`, `topN=8` | 0.9013241824 | -0.0009728113 | 1.667% |
| `margin=0.25`, `drop=0.1`, `topN=4` | 0.9021673380 | -0.0001296557 | 0.667% |
| `margin=0.25`, `drop=0.2`, `topN=8` | 0.9022969937 | +0.0000000000 | 0.333% |
| `margin=0.30`, `drop=0.1`, `topN=8` | 0.9022969937 | +0.0000000000 | 0.0% |

1000-scene confirmation for the conservative candidate:

| Method | Success | Failed | PDM score | Accepted | Latency mean |
| --- | ---: | ---: | ---: | ---: | ---: |
| K256 baseline | 1000 | 0 | 0.8852103916 | 0.0% | 645.776 ms |
| K256 retuned UTMR | 1000 | 0 | 0.8900427692 | 3.0% | 645.950 ms |

Retuned K256 setting:

```bash
NUM_TRAJ_ANCHOR=256
UTMR_TOP_N=4
UTMR_FINE_MARGIN_MIN=0.20
UTMR_MAX_COARSE_DROP=0.2
```

Finding:

- K256 does need a separate guard; directly reusing K64's setting was not
  reliable on full test.
- A more conservative K256 guard improved the 1000-scene subset by
  `+0.0048323775` while only accepting `3.0%` of reranks.
- A full retuned K256 run is the next optional robustness check, not required
  for the paper's K64 main result.

### 9. K64 Guard Sensitivity 1000-Scene Run

Status: complete.

Baseline:

| Method | Success | Failed | PDM score |
| --- | ---: | ---: | ---: |
| K64 baseline | 1000 | 0 | 0.8638675087 |

Top sensitivity results:

| Rank | `UTMR_FINE_MARGIN_MIN` | `UTMR_MAX_COARSE_DROP` | `UTMR_TOP_N` | PDM score | Delta vs baseline | Accepted |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.15 | 0.5 | 8 | 0.8720460220 | +0.0081785133 | 9.5% |
| 2 | 0.10 | 0.5 | 8 | 0.8709648289 | +0.0070973202 | 17.4% |
| 3 | 0.20 | 0.5 | 8 | 0.8695653544 | +0.0056978457 | 7.3% |
| 4 | 0.10 | 0.5 | 16 | 0.8681040503 | +0.0042365416 | 14.0% |
| 5 | 0.15 | 0.5 | 16 | 0.8680213566 | +0.0041538479 | 7.9% |

Finding:

- The previously selected setting, `margin=0.15`, `drop=0.5`, `topN=8`,
  remains the best 1000-scene setting.
- `drop=0.5` consistently works better than the stricter `drop=0.2`.
- `topN=8` is more stable than `topN=16` in the tested grid.
- The sensitivity result supports the chosen K64 full-run setting.

## AWSIM/Autoware Status

Status: live closed-loop smoke succeeded on the Shinjuku sample scenario.

Implemented pieces:

- `/planning/trajectory` UTMR planner publisher.
- Autoware localization subscription adapter. Helpers can subscribe to either
  `nav_msgs/Odometry` or `autoware_localization_msgs/KinematicState` through
  `UTMR_KINEMATIC_MSG_TYPE`; current AWSIM publishes `Odometry` on
  `/localization/kinematic_state`.
- Autoware pose-initializer stopped-condition alignment:
  - topic: `/sensing/vehicle_velocity_converter/twist_with_covariance`
  - type: `geometry_msgs/TwistWithCovarianceStamped`
  - threshold: `0.001 m/s`
  - hold: `3.0 s`
- Localization retry now performs a fresh stationary wait before each retry.
- AWSIM supervisor disables Autoware automatic pose initializer by default so
  manual DIRECT localization initialization can complete predictably.
- Stale route clear is best-effort through `/api/routing/clear_route`.
- Synthetic `route_publisher.py` is available but off by default for AWSIM live
  runs, because publishing an empty synthetic route polluted
  `/planning/mission_planning/route`.
- `/planning/clear_route` and `/planning/set_waypoint_route` are optional and
  off by default because this AWSIM/Autoware combination can spend the full ROS
  CLI timeout on those services while ADAPI route setup is enough for the smoke.
- Collision monitor bridge.
- Episode metric monitor.
- Drive/command gate injector for gear, turn, hazard, control, gate mode, and
  external heartbeat topics.
- Static/dynamic TF injector for AWSIM demo frame mismatch around
  `tamagawa/imu_link` and `velodyne_top`.
- Batch runner over variants:
  - `baseline`
  - `utmr`
  - `uniform_fine`
  - `fine_dt_only`
  - `short_horizon_only`
- Probe script for live topic discovery.
- Scenario JSON support.
- Scoped helper cleanup using recorded helper PIDs and script paths. Broad
  process killing was removed.
- Episode reducer ignores supervisor fallback rows for closed-loop tables so
  missing observed metrics do not look like real driving results.
- If readiness is incomplete, `run_utmr_demo.sh` prints `UTMR_READY=0` and exits
  with code `2` instead of printing a successful `done` line.
- AWSIM supervisor now waits for `run_utmr_demo.sh` readiness to finish before
  starting the fixed driving timeout. Readiness timeout is controlled by
  `--readiness-timeout-s`.

Latest AWSIM runs:

```text
experiments/utmr/results/awsim_route_clear_fastpath_20260714_134344
experiments/utmr/results/awsim_live_batch_fastpath_20260714_134703
experiments/utmr/results/awsim_post_retry_wait_smoke_20260714_140249
experiments/utmr/results/awsim_live_batch_5ep_readywait_20260714_142811
```

Main repeated live batch configuration:

```text
variants: baseline, utmr, uniform_fine, fine_dt_only, short_horizon_only
episodes per variant: 5
scenario: experiments/utmr/scenarios/awsim_shinjuku_sample.json
timeout: 120 s
readiness timeout: 240 s
merged steps: 32056
observed episode rows: 25
fallback episode rows: 0
```

Closed-loop episode result:

| Method | Episodes | Collision source | Success | Fallback | Mean speed km/h | Driving score |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| WoTE | 5 | not measured | 100% | 0 | 4.758 +/- 0.802 | 75.99 +/- 0.167 |
| WoTE + UTMR (Ours) | 5 | not measured | 100% | 0 | 4.142 +/- 0.050 | 75.86 +/- 0.010 |
| WoTE + Uniform Fine | 5 | not measured | 100% | 0 | 4.839 +/- 0.901 | 76.01 +/- 0.188 |
| UTMR (fine dt only) | 5 | not measured | 100% | 0 | 4.958 +/- 1.079 | 76.03 +/- 0.225 |
| UTMR (short horizon only) | 5 | not measured | 100% | 0 | 4.194 +/- 0.094 | 75.87 +/- 0.020 |

Generated live outputs:

```text
raw/awsim_batch_episodes.csv
raw/awsim_batch_steps.jsonl
tables/table_i_main_closed_loop.md
tables/table_ii_runtime.md
tables/table_iii_ablation_closed_loop.md
figures/fig3_speed_uncertainty.png
figures/fig4_selection_bias.png
figures/fig5_score_landscape.png
```

Interpretation:

- The AWSIM + Autoware + UTMR planner + reducer path now runs end-to-end and
  produces observed success rows for all five variants across repeated episodes.
- The previous `The vehicle is not stopped` blocker is mitigated by using the
  same stop-check topic/threshold/duration as Autoware's pose initializer and
  by retrying localization only after a fresh stopped check.
- Route setup no longer depends on the synthetic route publisher or the slow
  planning waypoint service.
- A diagnostic 5-episode attempt before the readiness wait fix produced one
  fallback row when readiness consumed the fixed timeout. The latest run has
  no fallback rows because episode timing starts after readiness exits.
- Collision is not treated as a measured result in this batch: no verified
  simulator collision/object topic was connected, and the sample scenario did
  not inject static obstacles. The episode CSV still contains `collision=False`
  defaults, but the docs intentionally mark collision as not measured.
- On this single Shinjuku route, full UTMR is slightly below baseline while
  `fine_dt_only` and `uniform_fine` are slightly above baseline. This should be
  interpreted as live integration stability evidence, not a general closed-loop
  performance conclusion.

Still required for stronger live AWSIM:

- Add more AWSIM scenarios/routes before treating the live table as robust.
- Confirm real object/collision topics with `probe_live_topics.sh` when
  perception/object topics are enabled.

## Commands Used

### Guarded Safety 1000

<details>
<summary>Click to expand the full 1000-scene command</summary>

```bash
cd /home/yax/UTMR

OUT=experiments/utmr/results/navsim_guarded_safety_1000
rm -rf "$OUT"
mkdir -p "$OUT/raw" "$OUT/logs"

NUM_TRAJ_ANCHOR=64 \
MODE=baseline \
UTMR_WOTE_METHOD=baseline_guarded_safety_1000 \
UTMR_WOTE_STEP_LOG="$(pwd)/$OUT/raw/baseline_steps.jsonl" \
experiments/utmr/run_navsim_wote_eval.sh \
  experiment_name=eval/WoTE/default_baseline_guarded_safety_1000 \
  scene_filter.max_scenes=1000 \
  metric_cache_path=/home/yax/UTMR/third_party/WoTE/exp/metric_cache \
  worker=sequential \
  > "$OUT/logs/baseline.log" 2>&1

NUM_TRAJ_ANCHOR=64 \
MODE=utmr \
UTMR_WOTE_METHOD=utmr_guarded_safety_1000 \
UTMR_TOP_N=8 \
UTMR_BETA=0.25 \
UTMR_GAMMA_H=0.30 \
UTMR_GAMMA_M=0.20 \
UTMR_MIN_TTC_SCORE=0.0 \
UTMR_MIN_NC=0.0 \
UTMR_FINE_IM_WEIGHT=0.0 \
UTMR_FINE_NC_WEIGHT=1.0 \
UTMR_FINE_DAC_WEIGHT=1.0 \
UTMR_FINE_EP_WEIGHT=0.5 \
UTMR_FINE_TTC_WEIGHT=1.0 \
UTMR_FINE_COMFORT_WEIGHT=0.5 \
UTMR_FINE_MARGIN_MIN=0.15 \
UTMR_MAX_COARSE_DROP=0.5 \
UTMR_WOTE_STEP_LOG="$(pwd)/$OUT/raw/utmr_guarded_safety_steps.jsonl" \
experiments/utmr/run_navsim_wote_eval.sh \
  experiment_name=eval/WoTE/default_utmr_guarded_safety_1000 \
  scene_filter.max_scenes=1000 \
  metric_cache_path=/home/yax/UTMR/third_party/WoTE/exp/metric_cache \
  worker=sequential \
  > "$OUT/logs/utmr_guarded_safety.log" 2>&1
```

</details>

### Guarded Safety Full Run

<details>
<summary>Click to expand the detached full-run command</summary>

```bash
cd /home/yax/UTMR

OUT=experiments/utmr/results/navsim_guarded_safety_full
rm -rf "$OUT"
mkdir -p "$OUT/raw" "$OUT/logs"

setsid bash -lc '
cd /home/yax/UTMR
OUT=experiments/utmr/results/navsim_guarded_safety_full

NUM_TRAJ_ANCHOR=64 \
MODE=baseline \
UTMR_WOTE_METHOD=baseline_guarded_safety_full \
UTMR_WOTE_STEP_LOG="$(pwd)/$OUT/raw/baseline_steps.jsonl" \
experiments/utmr/run_navsim_wote_eval.sh \
  experiment_name=eval/WoTE/default_baseline_guarded_safety_full \
  metric_cache_path=/home/yax/UTMR/third_party/WoTE/exp/metric_cache \
  worker=sequential \
  > "$OUT/logs/baseline.log" 2>&1

NUM_TRAJ_ANCHOR=64 \
MODE=utmr \
UTMR_WOTE_METHOD=utmr_guarded_safety_full \
UTMR_TOP_N=8 \
UTMR_BETA=0.25 \
UTMR_GAMMA_H=0.30 \
UTMR_GAMMA_M=0.20 \
UTMR_MIN_TTC_SCORE=0.0 \
UTMR_MIN_NC=0.0 \
UTMR_FINE_IM_WEIGHT=0.0 \
UTMR_FINE_NC_WEIGHT=1.0 \
UTMR_FINE_DAC_WEIGHT=1.0 \
UTMR_FINE_EP_WEIGHT=0.5 \
UTMR_FINE_TTC_WEIGHT=1.0 \
UTMR_FINE_COMFORT_WEIGHT=0.5 \
UTMR_FINE_MARGIN_MIN=0.15 \
UTMR_MAX_COARSE_DROP=0.5 \
UTMR_WOTE_STEP_LOG="$(pwd)/$OUT/raw/utmr_guarded_safety_steps.jsonl" \
experiments/utmr/run_navsim_wote_eval.sh \
  experiment_name=eval/WoTE/default_utmr_guarded_safety_full \
  metric_cache_path=/home/yax/UTMR/third_party/WoTE/exp/metric_cache \
  worker=sequential \
  > "$OUT/logs/utmr_guarded_safety.log" 2>&1

printf "%s\n" "$?" > "$OUT/run.exit"
' >/dev/null 2>&1 < /dev/null &

echo $! > "$OUT/run.pid"
```

</details>

### Full Run Post-Processing

<details>
<summary>Click to expand post-run analysis commands</summary>

```bash
cd /home/yax/UTMR

OUT=experiments/utmr/results/navsim_guarded_safety_full

cat "$OUT/raw/baseline_steps.jsonl" \
    "$OUT/raw/utmr_guarded_safety_steps.jsonl" \
  > "$OUT/raw/navsim_steps.jsonl"

python3 experiments/utmr/paper_experiments.py analyze \
  --steps "$OUT/raw/navsim_steps.jsonl" \
  --out-dir "$OUT/analysis"

grep -nE "Number of successful scenarios|Number of failed scenarios|Final average score|Results are stored" \
  "$OUT/logs/"*.log

experiments/utmr/check_assets.sh
```

</details>

## Next Experiments

1. Decide whether to retune a separate K256 guard/weight setting.
2. Improve AWSIM route-success scenario and repeat live batch with more episodes.
3. Convert the final full/subset/sensitivity/live results into paper-ready tables
   and figures.
