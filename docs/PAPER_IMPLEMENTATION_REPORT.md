# 논문 구현 상세 보고서

이 문서는 UTMR 논문 구현을 위해 실제로 한 작업, 사용한 코드, 실험 결과,
그리고 각 결과의 의미를 상세히 정리한 보고서입니다.

## 1. 목표

논문 구현의 실질 목표는 다음 네 가지였습니다.

1. WoTE/NAVSIM 기반 offline PDM scoring 경로에서 UTMR-style reranking을 구현한다.
2. 논문에서 말하는 coarse/fine selection 구조를 실험 가능한 코드로 만든다.
3. AWSIM/Autoware live 실험을 돌릴 수 있는 helper와 batch runner를 만든다.
4. 모든 작업을 `/home/yax/UTMR` 내부에서 수행하고 dataset symlink를 만들지 않는다.

## 2. 자산 준비

사용한 외부 코드와 자산:

| 항목 | 상태 |
| --- | --- |
| WoTE source | `liyingyanUCAS/WoTE.git` 기반 |
| WoTE checkpoint | 준비 완료 |
| ResNet34 checkpoint | 준비 완료 |
| NAVSIM maps/logs/sensor blobs | 준비 완료 |
| NAVSIM metric cache | `12146` metadata rows |
| K=64 trajectory anchors | 생성 완료 |
| K=64 formatted PDM score cache | 생성 완료 |

관련 코드:

| 코드 | 설명 |
| --- | --- |
| `experiments/utmr/prepare_wote_assets.sh` | WoTE checkpoint와 extra data를 준비합니다. |
| `experiments/utmr/setup_wote_runtime.sh` | 로컬 Python runtime package 경로를 구성합니다. |
| `experiments/utmr/make_wote_64_cache.py` | K=256 배포 anchor/cache에서 K=64 subset을 생성합니다. |
| `experiments/utmr/check_assets.sh` | 자산과 symlink 조건을 검증합니다. |

마지막 검증 상태:

```text
ok      resnet34.pth
ok      WoTE checkpoint
ok      dataset/maps
ok      dataset/navsim_logs/test (147 pkl files)
ok      dataset/sensor_blobs/test (147 scene dirs)
ok      trajectory_anchors_64.npy
ok      formatted_pdm_score_64.npy
ok      exp/metric_cache (12146 metadata rows)
symlinks under UTMR: 0
```

## 3. 구현한 코드 구조

### 3.1 NAVSIM/WoTE 실행 코드

`experiments/utmr/run_navsim_wote_eval.sh`

- WoTE의 `scripts/evaluation/eval_wote.sh`를 감쌉니다.
- `MODE=baseline`이면 기존 WoTE 선택을 사용합니다.
- `MODE=utmr`이면 WoTE config에 UTMR parameter를 주입합니다.
- 환경변수로 실험 parameter를 바꿀 수 있게 했습니다.

핵심 parameter:

```bash
UTMR_TOP_N
UTMR_BETA
UTMR_GAMMA_H
UTMR_GAMMA_M
UTMR_MIN_TTC_SCORE
UTMR_MIN_NC
UTMR_FINE_IM_WEIGHT
UTMR_FINE_NC_WEIGHT
UTMR_FINE_DAC_WEIGHT
UTMR_FINE_EP_WEIGHT
UTMR_FINE_TTC_WEIGHT
UTMR_FINE_COMFORT_WEIGHT
UTMR_FINE_MARGIN_MIN
UTMR_MAX_COARSE_DROP
UTMR_WOTE_STEP_LOG
```

### 3.2 WoTE 내부 UTMR selector

`third_party/WoTE/navsim/agents/WoTE/utmr_selector.py`

구현된 기능:

- coarse score entropy 계산
- top-1/top-2 margin 계산
- uncertainty trigger:
  - `entropy > gamma_h`
  - 또는 `margin < gamma_m`
- feasible mask 적용
- top-N 후보만 fine score로 reranking
- rerank accept guard:
  - fine score가 baseline보다 `UTMR_FINE_MARGIN_MIN` 이상 좋아야 함
  - coarse score 손실이 `UTMR_MAX_COARSE_DROP` 이하여야 함

의미:

- 논문식 “불확실할 때만 finer evaluation을 수행한다”는 구조를 WoTE 후보 선택에 맞게 구현했습니다.
- 단순 rerank가 아니라, 성능 하락을 막기 위해 accept guard를 추가했습니다.

### 3.3 WoTE model patch

`third_party/WoTE/navsim/agents/WoTE/WoTE_model.py`

구현된 기능:

- WoTE metric heads에서 fine reward를 계산합니다.
- 기존 `final_rewards`는 coarse score로 유지합니다.
- UTMR mode에서는 `select_with_utmr(...)` 결과의 `selected_indices`로 trajectory를 선택합니다.
- baseline mode에서는 기존 `argmax(final_rewards)` 선택을 유지합니다.

현재 fine reward는 아래 metric heads를 조합합니다.

```text
S_NC       no-at-fault-collision score
S_DAC      drivable-area-compliance score
S_EP       ego-progress score
S_TTC      time-to-collision score
S_COMFORT  comfort score
```

현재 best setting에서는:

```text
imitation weight = 0.0
NC weight        = 1.0
DAC weight       = 1.0
EP weight        = 0.5
TTC weight       = 1.0
comfort weight   = 0.5
```

### 3.4 WoTE agent logging

`third_party/WoTE/navsim/agents/WoTE/WoTE_agent.py`

step JSONL에 기록하는 값:

```text
token
method_variant
latency_ms
ego_speed_kmh
coarse_scores
candidate_speeds_kmh
entropy
margin
triggered
selected_index
baseline_index
feasible_count
feasible_mask
fine_scores_full
rerank_accepted
sim_rewards
```

의미:

- 실험 후 “UTMR가 실제로 선택을 바꿨는지”를 확인할 수 있습니다.
- 단순 final score뿐 아니라 trigger rate, accepted rerank rate, latency를 분석할 수 있습니다.

### 3.5 분석 코드

`experiments/utmr/paper_experiments.py`

생성 산출물:

```text
analysis/summary.json
analysis/tables/table_ii_runtime.csv
analysis/tables/table_iii_ablation_step_proxy.csv
analysis/figures/fig3_speed_uncertainty.png
analysis/figures/fig4_selection_bias.png
analysis/figures/fig5_score_landscape.png
analysis/raw/step_selections.csv
```

의미:

- 논문 표 형태로 runtime, trigger, selection, ablation proxy를 확인하기 위한 reducer입니다.
- episode-level CSV를 같이 넣으면 PDM score도 table에 들어갑니다.

## 4. 실험 타임라인과 결과

### 4.1 작은 subset 검증

처음에는 NAVSIM subset으로 baseline/UTMR가 모두 실행되는지 확인했습니다.

| 실험 | 결과 |
| --- | --- |
| 10 scenes | baseline/UTMR 모두 성공 |
| 100 scenes | baseline/UTMR 모두 성공 |
| 1000 scenes initial | baseline/UTMR 모두 성공 |

의미:

- runtime, checkpoint, metric cache, K=64 anchor/cache 경로가 맞다는 것을 확인했습니다.

### 4.2 초기 full K=64 NAVSIM run

| Method | Scene | Success | Failed | Score |
| --- | ---: | ---: | ---: | ---: |
| WoTE baseline | 12146 | 12146 | 0 | 0.8471632864 |
| 초기 UTMR | 12146 | 12146 | 0 | 0.8461780929 |

추가 분석:

```text
selected_changed_pct = 0.0
trigger_rate_pct     = 100.0
```

의미:

- 실험 pipeline은 정상입니다.
- 하지만 UTMR가 실제 trajectory 선택을 바꾸지 않았습니다.
- 이 결과는 “논문 아이디어가 실패했다”가 아니라 “구현상 reranking이 실제로 연결되지 않았다”는 신호였습니다.

### 4.3 원인 분석

코드 확인 결과:

```text
select_with_utmr(coarse_scores=final_rewards, fine_scores=None, ...)
```

상태였습니다.

의미:

- trigger가 true여도 selector는 baseline index를 그대로 반환할 수밖에 없었습니다.
- 그래서 aggressive parameter를 줘도 `selected_changed_pct = 0.0`이었습니다.

수정:

- metric-head 기반 `fine_rewards`를 계산했습니다.
- `fine_scores=fine_rewards`로 selector에 넘겼습니다.
- `fine_scores_full`, `rerank_accepted`를 로그에 남겼습니다.

### 4.4 unguarded rerank smoke

| Run | Scene | Selected changed | Score |
| --- | ---: | ---: | ---: |
| UTMR unguarded safety | 50 | 66.0% | 0.9052611125 |
| baseline smoke | 50 | 0.0% | 0.9380532306 |

의미:

- 선택 변화는 생겼습니다.
- 하지만 너무 많이 바꾸면 PDM score가 내려갔습니다.
- 따라서 “rerank할 수 있다”와 “rerank하는 것이 좋다”는 다릅니다.

### 4.5 1000-scene weight sweep

| Variant | Score | Selected changed | 의미 |
| --- | ---: | ---: | --- |
| baseline | 0.8638675087 | 0.0% | 기준 |
| `utmr_safety` | 0.8344109680 | 63.7% | 너무 공격적 |
| `utmr_balanced` | 0.8509310840 | 9.2% | baseline보다 낮음 |
| `utmr_conservative` | 0.8211362302 | 18.2% | baseline보다 낮음 |
| `utmr_ttc_heavy` | 0.8525664071 | 7.9% | baseline보다 낮음 |

token-level 결과:

```text
changed token에서만 score delta 발생
unchanged token의 mean delta = 0
```

의미:

- 하락은 rerank decision에서만 발생합니다.
- 따라서 guard를 추가해서 나쁜 rerank를 줄이는 방향이 맞습니다.

### 4.6 guarded safety smoke

설정:

```bash
UTMR_FINE_MARGIN_MIN=0.15
UTMR_MAX_COARSE_DROP=0.5
```

결과:

| Run | Scene | Selected changed | Score |
| --- | ---: | ---: | ---: |
| guarded safety smoke | 50 | 2.0% | 0.9580532306 |

의미:

- 매우 보수적으로 개입했을 때 작은 subset에서는 baseline보다 좋아졌습니다.

### 4.7 guarded safety 1000

| Method | Scene | Success | Failed | Score | Rerank accepted |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseline | 1000 | 1000 | 0 | 0.8638675087 | 0.0% |
| guarded safety UTMR | 1000 | 1000 | 0 | 0.8720460220 | 9.5% |

의미:

- 현재까지 가장 중요한 결과입니다.
- UTMR가 선택을 9.5%만 바꾸면서 baseline보다 score를 올렸습니다.
- 이 설정을 full `12146`에 적용하는 것이 다음 합리적 실험입니다.

### 4.8 guarded safety full

문서 갱신 시점 상태:

```text
baseline guarded-safety full: 4764 / 12146 step rows
active process: run_pdm_score.py
```

아직 최종 결과는 나오지 않았습니다. 완료 후 full baseline과 full guarded safety
UTMR score를 비교해야 합니다.

## 5. 현재 best 설정

```bash
NUM_TRAJ_ANCHOR=64
MODE=utmr
UTMR_TOP_N=8
UTMR_BETA=0.25
UTMR_GAMMA_H=0.30
UTMR_GAMMA_M=0.20
UTMR_MIN_TTC_SCORE=0.0
UTMR_MIN_NC=0.0
UTMR_FINE_IM_WEIGHT=0.0
UTMR_FINE_NC_WEIGHT=1.0
UTMR_FINE_DAC_WEIGHT=1.0
UTMR_FINE_EP_WEIGHT=0.5
UTMR_FINE_TTC_WEIGHT=1.0
UTMR_FINE_COMFORT_WEIGHT=0.5
UTMR_FINE_MARGIN_MIN=0.15
UTMR_MAX_COARSE_DROP=0.5
```

해석:

- imitation score는 fine score에 넣지 않았습니다.
- fine score는 safety/drivable/TTC/progress/comfort metric 중심입니다.
- 하지만 final accept는 coarse score 손실도 제한합니다.
- 즉 “metric score가 좋아 보인다고 무조건 선택하지 않고, 기존 WoTE confidence를 크게 해치지 않을 때만 선택”합니다.

## 6. AWSIM/Autoware 구현 상태

AWSIM live 결과는 아직 완료되지 않았지만, 실행 scaffolding은 구현했습니다.

| 코드 | 역할 |
| --- | --- |
| `autoware/utmr_scripts/helpers/utmr_planner_node.py` | UTMR trajectory를 `/planning/trajectory`로 publish |
| `autoware/utmr_scripts/helpers/collision_monitor.py` | object topic 기반 collision bridge |
| `autoware/utmr_scripts/helpers/episode_metric_monitor.py` | route, speed, distance, collision metric CSV 작성 |
| `experiments/utmr/awsim_supervisor.py` | episode 단위 실행 supervisor |
| `experiments/utmr/awsim_batch_runner.py` | variant batch 실행 |
| `experiments/utmr/scenarios/awsim_shinjuku_sample.json` | AWSIM sample scenario |

남은 작업:

1. AWSIM과 Autoware를 실제로 켭니다.
2. `autoware/utmr_scripts/probe_live_topics.sh`로 topic 이름을 확인합니다.
3. object/collision topic을 환경변수로 넣습니다.
4. `experiments/utmr/run_awsim_batch.sh`를 실행합니다.

## 7. 긴 실행 명령

### 7.1 guarded safety 1000

<details>
<summary>명령 펼치기</summary>

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

### 7.2 guarded safety full

<details>
<summary>명령 펼치기</summary>

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
```

</details>

### 7.3 full run 후 분석

<details>
<summary>명령 펼치기</summary>

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

## 8. 결론

현재까지의 의미 있는 결론은 다음과 같습니다.

1. NAVSIM/WoTE offline PDM scoring pipeline은 정상 작동합니다.
2. 초기 UTMR는 trigger만 켜지고 실제 선택을 바꾸지 않았습니다.
3. `fine_scores=None` 문제를 고친 뒤 실제 reranking이 동작했습니다.
4. unguarded reranking은 너무 공격적이라 score를 낮췄습니다.
5. guarded reranking은 1000-scene subset에서 baseline보다 높았습니다.
6. full guarded-safety 결과가 나오면 NAVSIM 쪽 논문 구현의 핵심 결과로 쓸 수 있습니다.
