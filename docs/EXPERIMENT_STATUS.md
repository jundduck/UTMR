# UTMR Experiment Status

Last updated: 2026-07-12 KST.

## Scope

The implementation work is focused on reproducing the UTMR-style experiments
from the workshop paper using:

- NAVSIM/WoTE offline PDM scoring.
- WoTE released checkpoint and NAVSIM metric cache.
- AWSIM/Autoware live integration scaffolding.

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

| Method | Success | Failed | PDM score | Selected changed / accepted |
| --- | ---: | ---: | ---: | ---: |
| baseline | 1000 | 0 | 0.8638675087 | 0.0% |
| guarded safety UTMR | 1000 | 0 | 0.8720460220 | 9.5% |

This is the best current NAVSIM result and the reason the full guarded-safety
run was started.

### 6. Guarded Safety Full Run

Status at documentation time: running.

Observed state:

```text
baseline guarded-safety full: 4764 / 12146 step rows
active process: run_pdm_score.py
```

Expected output folder:

```text
experiments/utmr/results/navsim_guarded_safety_full
```

After completion, compare full baseline and guarded-safety UTMR scores using the
post-run commands below.

## AWSIM/Autoware Status

Status: implemented scaffolding, live batch pending.

Implemented pieces:

- `/planning/trajectory` UTMR planner publisher.
- Autoware localization subscription adapter.
- AWSIM object topic adapter for predicted/tracked/detected objects.
- Collision monitor bridge.
- Episode metric monitor.
- Batch runner over variants:
  - `baseline`
  - `utmr`
  - `uniform_fine`
  - `fine_dt_only`
  - `short_horizon_only`
- Probe script for live topic discovery.
- Scenario JSON support.

Still required for live AWSIM:

- Start AWSIM + Autoware.
- Run `autoware/utmr_scripts/probe_live_topics.sh`.
- Confirm real object/collision topics.
- Run `experiments/utmr/run_awsim_batch.sh`.

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

1. Finish and analyze the guarded-safety full `12146` run.
2. If full guarded-safety beats full baseline, repeat with K=256 original WoTE
   anchors/cache.
3. Run UTMR sensitivity around:
   - `UTMR_FINE_MARGIN_MIN = 0.10, 0.15, 0.20`
   - `UTMR_MAX_COARSE_DROP = 0.2, 0.5`
   - `UTMR_TOP_N = 8, 16`
4. Run AWSIM/Autoware live batch after topic probing.
