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

full `12146`-scenario 평가도 완료됐습니다.

| Method | Scene | Success | Failed | Score | Rerank accepted |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseline | 12146 | 12146 | 0 | 0.8471632864 | 0.0% |
| guarded safety UTMR | 12146 | 12146 | 0 | 0.8542971577 | 9.8139% |

추가 진단:

```text
trigger_rate_pct        100.0
selected_changed_pct    9.81393051210275
rerank_accepted_pct     9.81393051210275
fine_score_coverage_pct 100.0
latency_mean_ms         308.85380620006794
latency_p99_ms          338.6317099993903
```

의미:

- full test set에서도 guarded safety UTMR가 baseline보다 높았습니다.
- score 개선폭은 `+0.0071338713`입니다.
- 선택을 전부 바꾼 것이 아니라, 약 9.8%의 step만 rerank했습니다.
- 따라서 현재 구현의 핵심 주장은 “fine metric 기반 재선택을 guard 없이
  무조건 적용하면 위험하지만, coarse confidence 손실을 제한하면 full set에서도
  개선 가능하다”입니다.

### 4.9 K256 원본 WoTE anchor full 검증

논문 본문 설정은 `K=64`이지만, WoTE 공개 자산에는 `K=256` anchor/cache가
포함되어 있습니다. 그래서 같은 guarded safety 설정이 더 강한 원본 후보군에서도
유지되는지 추가 확인했습니다.

| Method | Scene | Success | Failed | Score | Rerank accepted |
| --- | ---: | ---: | ---: | ---: | ---: |
| K256 baseline | 12146 | 12146 | 0 | 0.8833150351 | 0.0% |
| K256 guarded safety UTMR | 12146 | 12146 | 0 | 0.8827077445 | 8.1014% |

추가 진단:

```text
baseline latency_mean_ms      616.9268
baseline latency_p99_ms       667.6763
utmr latency_mean_ms          627.1611
utmr latency_p99_ms           680.0303
utmr fine_score_coverage_pct  100.0
```

의미:

- K256 baseline은 K64 baseline보다 훨씬 높습니다.
- K64에서 선택한 guard를 그대로 쓰면 K256에서는 baseline보다
  `-0.0006072906` 낮습니다.
- 따라서 K64 논문 설정에서는 UTMR 효과가 확인됐지만, K256 원본 anchor로
  확장하려면 별도 guard/weight retuning이 필요합니다.

### 4.10 K256 retuned guard subset

K256 full에서 같은 guard가 살짝 낮았기 때문에, full을 다시 태우기 전에
작은 subset으로 별도 K256 guard를 탐색했습니다.

300-scene sweep:

| Setting | Score | Delta vs baseline | Rerank accepted |
| --- | ---: | ---: | ---: |
| baseline | 0.9022969937 | +0.0000000000 | 0.0% |
| `margin=0.15, drop=0.5, topN=8` | 0.9034554556 | +0.0011584619 | 5.0% |
| `margin=0.20, drop=0.2, topN=4` | 0.9033675968 | +0.0010706030 | 1.0% |
| `margin=0.20, drop=0.2, topN=8` | 0.9013241824 | -0.0009728113 | 1.667% |
| `margin=0.25, drop=0.1, topN=4` | 0.9021673380 | -0.0001296557 | 0.667% |

1000-scene confirmation:

| Method | Scene | Success | Failed | Score | Rerank accepted |
| --- | ---: | ---: | ---: | ---: | ---: |
| K256 baseline | 1000 | 1000 | 0 | 0.8852103916 | 0.0% |
| K256 retuned UTMR | 1000 | 1000 | 0 | 0.8900427692 | 3.0% |

K256 retuned setting:

```bash
NUM_TRAJ_ANCHOR=256
UTMR_TOP_N=4
UTMR_FINE_MARGIN_MIN=0.20
UTMR_MAX_COARSE_DROP=0.2
```

의미:

- K256은 K64보다 baseline이 강해서 더 보수적인 guard가 필요합니다.
- `margin=0.20`, `drop=0.2`, `topN=4`는 1000-scene subset에서
  `+0.0048323775` 개선됐고, rerank accepted는 `3.0%`로 낮았습니다.
- K256 retuned full run은 optional robustness check로 남아 있습니다.

### 4.11 K64 guarded sensitivity 1000

`UTMR_FINE_MARGIN_MIN`, `UTMR_MAX_COARSE_DROP`, `UTMR_TOP_N` 조합을 바꿔
1000-scene sensitivity를 돌렸습니다.

| Rank | Margin | Max coarse drop | Top-N | Score | Delta vs baseline | Rerank accepted |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | - | - | - | 0.8638675087 | +0.0000000000 | 0.0% |
| 1 | 0.15 | 0.5 | 8 | 0.8720460220 | +0.0081785133 | 9.5% |
| 2 | 0.10 | 0.5 | 8 | 0.8709648289 | +0.0070973202 | 17.4% |
| 3 | 0.20 | 0.5 | 8 | 0.8695653544 | +0.0056978457 | 7.3% |
| 4 | 0.10 | 0.5 | 16 | 0.8681040503 | +0.0042365416 | 14.0% |
| 5 | 0.15 | 0.5 | 16 | 0.8680213566 | +0.0041538479 | 7.9% |

의미:

- 기존 best인 `margin=0.15`, `drop=0.5`, `topN=8`이 그대로 1등입니다.
- `drop=0.2`는 너무 보수적이라 대부분 개선폭이 작습니다.
- `topN=16`은 후보를 더 많이 보지만, 이번 grid에서는 `topN=8`보다 낮았습니다.
- 이 sensitivity는 K64 full run에 쓴 설정 선택을 뒷받침합니다.

## 5. 현재 best 설정

K64 논문 본문 설정:

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

K256 추가 검증용 보수 설정:

```bash
NUM_TRAJ_ANCHOR=256
MODE=utmr
UTMR_TOP_N=4
UTMR_FINE_MARGIN_MIN=0.20
UTMR_MAX_COARSE_DROP=0.2
```

해석:

- imitation score는 fine score에 넣지 않았습니다.
- fine score는 safety/drivable/TTC/progress/comfort metric 중심입니다.
- 하지만 final accept는 coarse score 손실도 제한합니다.
- 즉 “metric score가 좋아 보인다고 무조건 선택하지 않고, 기존 WoTE confidence를 크게 해치지 않을 때만 선택”합니다.

## 6. AWSIM/Autoware 구현 상태

AWSIM live path도 실행했습니다. 초기에는 localization init이
`The vehicle is not stopped.`로 실패하거나 route success가 `0%`였지만,
현재는 Autoware stopped-condition을 맞추고 route fastpath를 정리해
AWSIM + Autoware + UTMR planner + reducer가 observed closed-loop row를
만드는 상태까지 도달했습니다. 추가로 turn-guidance smoke에서 route waypoint를
planner route guidance로 주입해 live planner가 직진-only trajectory에 고정되지
않는 것을 확인했습니다.

| 코드 | 역할 |
| --- | --- |
| `autoware/utmr_scripts/helpers/utmr_planner_node.py` | UTMR trajectory를 `/planning/trajectory`로 publish |
| `autoware/utmr_scripts/helpers/route_publisher.py` | synthetic route publisher. 현재 AWSIM 기본값에서는 빈 route topic 오염을 피하려고 off |
| `autoware/utmr_scripts/helpers/drive_gear_injector.py` | gear/turn/hazard/control/gate/heartbeat command를 publish |
| `autoware/utmr_scripts/helpers/wait_for_stationary.py` | Autoware pose initializer와 같은 stop-check topic/threshold/hold로 localization init 전 정지 확인 |
| `autoware/utmr_scripts/helpers/static_tf_injector.py` | AWSIM demo frame mismatch 완화를 위해 static/dynamic TF publish |
| `autoware/utmr_scripts/helpers/collision_monitor.py` | object topic 기반 collision bridge, `Odometry`/`KinematicState` localization input 선택 지원 |
| `autoware/utmr_scripts/helpers/episode_metric_monitor.py` | route, speed, distance, collision metric CSV 작성, AWSIM `nav_msgs/Odometry` topic 지원 |
| `autoware/utmr_scripts/helpers/helper_shutdown.py` | 정상 shutdown 중 발생하는 ROS context error만 제한적으로 suppress |
| `autoware/utmr_scripts/run_utmr_demo.sh` | Autoware service retry/order, localization `success=True` response 확인, retry마다 stationary wait 재수행 |
| `autoware/utmr_scripts/run_straight_demo.sh` | straight trajectory smoke 실행, shared readiness helper로 gate unstop fail-closed 처리 |
| `autoware/utmr_scripts/service_calls.sh` | service response pattern 검증과 재시도 |
| `autoware/utmr_scripts/service_readiness.sh` | localization/route/operation/gate readiness 순서 실행, operation 실패 시 gate unstop skip |
| `experiments/utmr/test_service_calls.sh` | production readiness 함수를 fake-ROS로 검증, localization 실패와 operation 실패 모두 gate skip 확인 |
| `experiments/utmr/awsim_supervisor.py` | episode 단위 실행 supervisor |
| `experiments/utmr/awsim_batch_runner.py` | variant batch 실행 |
| `experiments/utmr/scenarios/awsim_shinjuku_sample.json` | AWSIM sample scenario |
| `experiments/utmr/scenarios/awsim_shinjuku_turn_sample.json` | non-straight route-guidance smoke scenario |

추가로 보강한 점:

- ROS helper node가 정상 shutdown 중 남기던 `ExternalShutdownException`,
  `RCLError` trace를 정상 종료로 처리했습니다.
- episode CSV가 비어 있으면 supervisor가 fallback row를 쓰되,
  `metric_source=fallback`으로 표시합니다.
- `paper_experiments.py`는 closed-loop table에서 fallback row를 제외합니다.
- helper cleanup은 기록된 PID와 helper script path만 대상으로 하도록 제한했습니다.
- synthetic route publisher는 AWSIM 기본값에서 off로 바꿨습니다. 빈 route
  topic이 `/planning/mission_planning/route`를 오염시키지 않게 하기 위해서입니다.
- drive injector로 command gate warmup 경고를 줄였습니다.
- static/dynamic TF injector로 `tamagawa/imu_link`, `velodyne_top` transform
  경고를 크게 줄였습니다.
- runtime topic probe에서 `/localization/kinematic_state`의 publisher가
  `nav_msgs/Odometry`임을 확인했고, helper들이 해당 타입을 받을 수 있게
  adapter를 추가했습니다.
- `/planning/clear_route`와 `/planning/set_waypoint_route`는 기본 off로 두었습니다.
  이 AWSIM/Autoware 조합에서는 두 서비스가 ROS CLI timeout을 크게 잡아먹고,
  ADAPI route service만으로 smoke에는 충분했습니다.
- scenario `route_waypoints`를 ADAPI route request와 UTMR planner route
  guidance에 모두 전달합니다.
- `allow_synthetic_route_fallback: true`인 smoke/debug scenario에서는 ADAPI
  route setup이 fail-closed 된 뒤 planner-only synthetic route publisher를
  켭니다. 이 fallback은 route_ready를 위조하거나 gate unstop을 보내지
  않습니다. 기본 benchmark path에서는 계속 off입니다.
- localization service는 shell exit code가 아니라 응답의 `success=True`까지
  확인합니다.
- localization retry마다 fresh stationary wait를 다시 수행합니다.
- AWSIM supervisor는 Autoware automatic pose initializer를 기본 비활성화해서
  manual DIRECT localization initialize가 먼저 완료되도록 했습니다.
- localization이 실패하거나 route가 준비되지 않으면 autonomous mode와 vehicle
  gate unstop 호출을 건너뜁니다.
- readiness가 완성되지 않으면 `UTMR_READY=0`와 exit code `2`를 남겨 smoke
  실패가 조용히 성공처럼 보이지 않게 했습니다.
- supervisor는 `run_utmr_demo.sh` readiness가 종료된 뒤에만 driving timeout을
  시작합니다. 이 대기는 `--readiness-timeout-s`로 조절합니다.

최신 live batch:

```text
experiments/utmr/results/awsim_live_batch_5ep_readywait_20260714_142811
```

Turn-guidance smoke:

```text
experiments/utmr/results/awsim_turn_guidance_smoke_20260714_164514
scenario: experiments/utmr/scenarios/awsim_shinjuku_turn_sample.json
UTMR_READY: 1 in the recorded smoke, before later safety hardening
step rows: 524
route_guided rows: 524 / 524
route_target_y_m range: 2.6561 .. 10.2924
distance_m: 5.3072
success: False
timeout: True
```

This is not a final performance result. It was added to debug the observed
"drives straight" behavior. The ADAPI route service kept returning
`The route is already set`, so the smoke used explicit synthetic route fallback.
The useful evidence is that `/planning/trajectory` publication was route-guided
for every recorded step. The later safety hardening keeps synthetic fallback
planner-only unless the route service succeeds.

| Method | Episodes | Collision source | Success | Fallback | Mean speed km/h | Driving score |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| WoTE | 5 | not measured | 100% | 0 | 4.758 +/- 0.802 | 75.99 +/- 0.167 |
| WoTE + UTMR (Ours) | 5 | not measured | 100% | 0 | 4.142 +/- 0.050 | 75.86 +/- 0.010 |
| WoTE + Uniform Fine | 5 | not measured | 100% | 0 | 4.839 +/- 0.901 | 76.01 +/- 0.188 |
| UTMR (fine dt only) | 5 | not measured | 100% | 0 | 4.958 +/- 1.079 | 76.03 +/- 0.225 |
| UTMR (short horizon only) | 5 | not measured | 100% | 0 | 4.194 +/- 0.094 | 75.87 +/- 0.020 |

Generated outputs:

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

해석:

- live batch는 5 variants x 5 episodes 모두 observed metric row를 만들었습니다.
- fallback episode row는 `0`입니다.
- merged planner step log는 `32056` rows입니다.
- Odometry adapter는 metric monitor가 live localization을 받는 것을 확인했습니다.
- helper tracebacks, leftover AWSIM/Autoware/UTMR processes, UTMR symlinks는
  최종 확인 기준 `0`입니다.
- stopped-condition, route fastpath, command gate 순서 문제는 smoke 기준 해결됐습니다.
- readiness가 episode driving timeout을 갉아먹는 문제가 supervisor wait로
  해결됐습니다. 이전 진단 batch에서는 readiness/route setup 지연 때문에
  fallback row가 섞였지만, 최신 repeated batch에서는 fallback이 없습니다.
- 이 batch에는 검증된 simulator collision/object topic이 연결되지 않았고,
  sample scenario도 static obstacle을 주입하지 않았습니다. 따라서 episode
  CSV의 `collision=False` 기본값은 collision 성능 metric으로 사용하지 않고,
  표에서는 `not measured`로 기록합니다.
- Shinjuku sample route 1개 기준으로는 full UTMR가 baseline보다 약간 낮고,
  `fine_dt_only`와 `uniform_fine`이 근소하게 높았습니다. 따라서 이 live 결과는
  통합 안정화 증거이며, 일반 성능 결론은 추가 scenario가 필요합니다.

남은 작업:

1. 추가 AWSIM scenario/route를 만들어 결과가 한 route에만 묶이지 않게 합니다.
2. perception/object topic을 켠 상태에서 `probe_live_topics.sh`로 topic을 확정합니다.
3. K256 retuned guard를 full `12146`으로 확대할지 결정합니다.

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
6. guarded reranking은 full `12146`-scenario 평가에서도 baseline보다 높았습니다.
7. K64 sensitivity에서도 `margin=0.15`, `drop=0.5`, `topN=8`이 가장 좋았습니다.
8. K256 원본 anchor에서는 같은 guard가 baseline보다 약간 낮았지만, 보수 guard는
   1000-scene subset에서 baseline보다 높았습니다.
9. AWSIM/Autoware live path는 stopped-condition, route fastpath, service-order,
   readiness wait를 지나 5개 variant x 5 episodes 모두 observed success row를
   만들었습니다. fallback row는 `0`입니다.
10. 남은 것은 AWSIM scenario 다양화와 optional K256 retuned full run입니다.
