# UTMR 논문 구현 및 실험 기록

이 저장소는 IV 2026 Autoware Workshop 논문 실험을 재현하기 위해
`/home/yax/UTMR`에서 구현한 코드와 실험 기록을 정리한 것입니다.

대용량 파일은 포함하지 않았습니다. NAVSIM 데이터셋, sensor blobs,
metric cache, WoTE checkpoint, AWSIM binary, Autoware build/install 결과,
raw JSONL/CSV 로그는 로컬에만 있고 GitHub에는 올리지 않았습니다.

자세한 구현/실험 보고서는 아래 문서에 정리했습니다.

- [docs/PAPER_IMPLEMENTATION_REPORT.md](docs/PAPER_IMPLEMENTATION_REPORT.md)
- [docs/EXPERIMENT_STATUS.md](docs/EXPERIMENT_STATUS.md)
- [docs/EXPERIMENT_MATRIX.md](docs/EXPERIMENT_MATRIX.md)

## 한 줄 요약

현재까지는 **NAVSIM/WoTE 오프라인 PDM scoring 경로를 중심으로 UTMR
reranking을 구현했고**, full `12146`-scenario 평가에서도 guarded safety
UTMR가 baseline보다 높은 결과를 냈습니다.

| 실험 | Scene / episode | 성공/실패 | Score | 의미 |
| --- | ---: | ---: | ---: | --- |
| K64 WoTE baseline | 12146 | 12146 / 0 | 0.8471632864 | 논문 설정 full 기준선 |
| K64 UTMR guarded safety | 12146 | 12146 / 0 | 0.8542971577 | 논문 설정 full 개선 |
| K256 WoTE baseline | 12146 | 12146 / 0 | 0.8833150351 | 원본 WoTE anchor 기준선 |
| K256 UTMR guarded safety | 12146 | 12146 / 0 | 0.8827077445 | 같은 guard로는 약간 낮음 |
| WoTE baseline | 1000 | 1000 / 0 | 0.8638675087 | subset 기준선 |
| UTMR guarded safety | 1000 | 1000 / 0 | 0.8720460220 | tuning용 subset 결과 |
| AWSIM live batch | 5 episodes | 0 success / 0 collision | 0.0 driving score | live pipeline smoke, route success 미확보 |

`UTMR guarded safety`는 baseline 후보를 무조건 바꾸지 않고, fine metric
score가 충분히 좋아지고 coarse score 손실이 제한될 때만 rerank를 받아들이는
방식입니다. 이 설정에서 full 기준 `rerank_accepted_pct = 9.8139%`였습니다.

논문 본문 설정은 `K=64`입니다. `K=256` 실험은 논문 설정이 아니라,
WoTE 공개 자산의 원본 anchor 수에서도 같은 guard가 유지되는지 확인하기 위한
추가 검증입니다. 결과적으로 K64에서는 개선됐고, K256에서는 baseline보다
`0.0006072906` 낮아 K256용 guard/weight는 별도 retuning이 필요합니다.

## 무엇을 구현했나

### 1. NAVSIM/WoTE 실험 파이프라인

| 코드 | 역할 |
| --- | --- |
| `experiments/utmr/run_navsim_wote_eval.sh` | WoTE evaluation wrapper. `MODE=baseline/utmr`, UTMR fine score weight, guard threshold를 환경변수로 주입합니다. |
| `experiments/utmr/paper_experiments.py` | step log를 읽어 runtime/ablation/selection table과 figure를 만듭니다. |
| `experiments/utmr/check_assets.sh` | WoTE checkpoint, NAVSIM logs/sensors/maps, metric cache, symlink 수를 확인합니다. |
| `experiments/utmr/make_wote_64_cache.py` | WoTE K=256 배포 asset에서 논문 실험용 K=64 anchor/cache를 만듭니다. |
| `experiments/utmr/setup_wote_runtime.sh` | 로컬 runtime package 경로를 구성합니다. |

### 2. WoTE 내부 UTMR reranking 패치

| 코드 | 역할 |
| --- | --- |
| `third_party/WoTE/navsim/agents/WoTE/utmr_selector.py` | entropy/margin trigger, top-N rerank, rerank accept guard를 수행합니다. |
| `third_party/WoTE/navsim/agents/WoTE/WoTE_model.py` | WoTE forward pass에서 metric-head 기반 UTMR fine score를 계산하고 최종 trajectory 선택에 반영합니다. |
| `third_party/WoTE/navsim/agents/WoTE/WoTE_agent.py` | `selected_index`, `baseline_index`, `fine_scores_full`, `rerank_accepted` 같은 진단 로그를 JSONL로 남깁니다. |
| `third_party/WoTE/navsim/agents/WoTE/configs/default.py` | UTMR parameter 기본값을 추가합니다. |

초기 구현에서는 UTMR trigger가 켜져도 `fine_scores=None`으로 selector가 호출되어
실제 reranking이 일어나지 않았습니다. 이 문제를 고쳐 fine score를 넘기고,
너무 공격적인 rerank가 score를 깎지 않도록 guard를 추가했습니다.

### 3. AWSIM/Autoware live batch

AWSIM/Autoware live batch도 실제로 실행했습니다. 다만 현재 sample scenario는
route arrival이 잡히지 않아 모든 variant가 timeout으로 끝났습니다. 따라서 이
결과는 성능 비교라기보다 “AWSIM + Autoware + UTMR planner + metric reducer가
한 번에 도는지”를 확인한 live integration smoke에 가깝습니다.

| 코드 | 역할 |
| --- | --- |
| `autoware/utmr_scripts/helpers/utmr_planner_node.py` | Autoware `/planning/trajectory`로 UTMR trajectory를 publish합니다. |
| `autoware/utmr_scripts/helpers/collision_monitor.py` | object topic 기반 collision bool bridge입니다. |
| `autoware/utmr_scripts/helpers/episode_metric_monitor.py` | speed, distance, route arrival, collision을 episode CSV로 기록합니다. |
| `experiments/utmr/awsim_supervisor.py` | Autoware/AWSIM helper process를 묶어 episode 단위로 실행합니다. |
| `experiments/utmr/awsim_batch_runner.py` | baseline, utmr, ablation variant를 batch로 실행합니다. |
| `autoware/utmr_scripts/probe_live_topics.sh` | 실제 AWSIM/Autoware topic 이름을 probe합니다. |

최신 live batch:

```text
experiments/utmr/results/awsim_live_batch_metric_20260714_092655
```

| Method | Episodes | Collision | Success | Mean speed | Driving score |
| --- | ---: | ---: | ---: | ---: | ---: |
| WoTE | 1 | 0% | 0% | 28.8 km/h | 0.0 |
| WoTE + UTMR (Ours) | 1 | 0% | 0% | 28.8 km/h | 0.0 |
| WoTE + Uniform Fine | 1 | 0% | 0% | 28.8 km/h | 0.0 |
| UTMR fine dt only | 1 | 0% | 0% | 28.8 km/h | 0.0 |
| UTMR short horizon only | 1 | 0% | 0% | 28.8 km/h | 0.0 |

Runtime table from the same live step logs:

| Method | Trigger rate | Mean latency | P99 latency |
| --- | ---: | ---: | ---: |
| WoTE coarse | 0% | 6.49 ms | 20.93 ms |
| WoTE + UTMR | 100% | 13.46 ms | 34.74 ms |
| WoTE + Uniform Fine | 100% | 13.93 ms | 40.03 ms |

## 어떤 실험을 했고 어떤 결과가 나왔나

### A. 초기 full K=64 NAVSIM baseline vs UTMR

| Method | Scene | Success | Failed | Score |
| --- | ---: | ---: | ---: | ---: |
| WoTE baseline | 12146 | 12146 | 0 | 0.8471632864 |
| 초기 UTMR | 12146 | 12146 | 0 | 0.8461780929 |

의미:

- 파이프라인은 정상 동작했습니다.
- 하지만 `selected_changed_pct = 0.0`이었습니다.
- 즉 UTMR가 trigger는 켰지만 실제 trajectory 선택은 baseline과 같았습니다.
- 원인은 WoTE model에서 `select_with_utmr(..., fine_scores=None)`으로 호출되어
  reranking이 불가능했던 것입니다.

### B. reranking 수정 후 smoke

| Run | Scene | Selected changed | Score | 의미 |
| --- | ---: | ---: | ---: | --- |
| unguarded safety smoke | 50 | 66.0% | 0.9052611125 | 선택은 많이 바뀌지만 너무 공격적 |
| guarded safety smoke | 50 | 2.0% | 0.9580532306 | 보수적 rerank가 더 안정적 |

의미:

- 수정 후 UTMR가 실제로 trajectory 선택을 바꾸기 시작했습니다.
- 단순히 많이 바꾸는 것은 좋은 결과로 이어지지 않았습니다.
- 따라서 `fine score 개선량`과 `coarse score 손실`을 같이 보는 guard가 필요했습니다.

### C. 1000-scene weight sweep

| Variant | Score | Selected changed | 의미 |
| --- | ---: | ---: | --- |
| baseline | 0.8638675087 | 0.0% | 기준선 |
| `utmr_safety` | 0.8344109680 | 63.7% | 너무 많이 바꿔서 손해 |
| `utmr_balanced` | 0.8509310840 | 9.2% | 선택 변화는 적지만 아직 손해 |
| `utmr_conservative` | 0.8211362302 | 18.2% | 손해 |
| `utmr_ttc_heavy` | 0.8525664071 | 7.9% | baseline보다 낮음 |

의미:

- token-level 분석 결과, 점수 하락은 rerank된 token에서만 발생했습니다.
- 바뀌지 않은 token은 baseline과 동일한 score였습니다.
- 그래서 UTMR 자체를 끄는 것이 아니라, rerank를 받을 조건을 더 엄격하게
  만드는 방향이 맞다고 판단했습니다.

### D. guarded safety 1000-scene 결과

| Method | Scene | Success | Failed | Score | Rerank accepted |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseline | 1000 | 1000 | 0 | 0.8638675087 | 0.0% |
| guarded safety UTMR | 1000 | 1000 | 0 | 0.8720460220 | 9.5% |

의미:

- 현재까지 가장 좋은 NAVSIM subset 결과입니다.
- UTMR가 모든 상황에서 개입하는 것이 아니라, fine metric score가 좋아지고
  coarse score 손실이 제한되는 경우만 선택을 바꿨을 때 성능이 좋아졌습니다.
- 이 결과를 바탕으로 full `12146` guarded safety 실험을 실행했습니다.

### E. guarded safety full run

full run도 완료됐습니다.

| Method | Scene | Success | Failed | Score | Rerank accepted |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseline | 12146 | 12146 | 0 | 0.8471632864 | 0.0% |
| guarded safety UTMR | 12146 | 12146 | 0 | 0.8542971577 | 9.8139% |

의미:

- full NAVSIM test set에서도 guarded safety UTMR가 baseline보다 높았습니다.
- score 차이는 `+0.0071338713`입니다.
- UTMR는 모든 step을 바꾸지 않고 약 9.8%만 rerank했습니다.
- 분석 산출물은 로컬 `experiments/utmr/results/navsim_guarded_safety_full/analysis`
  아래에 생성했습니다.

```text
baseline csv: /home/yax/UTMR/third_party/WoTE/exp/eval/WoTE/default_baseline_guarded_safety_full/2026.07.13.00.16.03.csv
utmr csv:     /home/yax/UTMR/third_party/WoTE/exp/eval/WoTE/default_utmr_guarded_safety_full/2026.07.13.01.45.04.csv
```

### F. K256 원본 WoTE anchor full 검증

| Method | Scene | Success | Failed | Score | Rerank accepted |
| --- | ---: | ---: | ---: | ---: | ---: |
| K256 baseline | 12146 | 12146 | 0 | 0.8833150351 | 0.0% |
| K256 guarded safety UTMR | 12146 | 12146 | 0 | 0.8827077445 | 8.1014% |

의미:

- K256 baseline 자체가 K64 baseline보다 훨씬 강합니다.
- K64에서 찾은 guard를 그대로 K256에 적용하면 `-0.0006072906` 낮았습니다.
- 따라서 논문 설정인 K64 결과는 성공이지만, K256 확장에는 별도 tuning이
  필요합니다.

### G. K64 sensitivity 1000-scene 결과

| Rank | Setting | Score | Delta vs baseline | Rerank accepted |
| ---: | --- | ---: | ---: | ---: |
| 1 | `margin=0.15, drop=0.5, topN=8` | 0.8720460220 | +0.0081785132 | 9.5% |
| 2 | `margin=0.10, drop=0.5, topN=8` | 0.8709648289 | +0.0070973202 | 17.4% |
| 3 | `margin=0.20, drop=0.5, topN=8` | 0.8695653544 | +0.0056978457 | 7.3% |
| baseline | WoTE baseline | 0.8638675087 | +0.0000000000 | 0.0% |

의미:

- 기존 best 설정 `margin=0.15, drop=0.5, topN=8`이 sensitivity에서도 1등입니다.
- `drop=0.5`가 `drop=0.2`보다 전반적으로 좋았습니다.
- `topN=8`이 `topN=16`보다 안정적이었습니다.
- 너무 엄격한 guard는 거의 바꾸지 못하고, 너무 넓은 top-N은 불필요한 후보까지
  끌어와 score를 낮출 수 있습니다.

## 현재 best UTMR 설정

```bash
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

## 다음에 해야 할 일

1. K256용 guard/weight를 별도 retuning할지 결정.
2. AWSIM scenario를 route success가 잡히는 형태로 개선하고 5+ episode 반복 실행.
3. 논문 표/그림용 최종 정리: K64 full PDM score, K256 check, sensitivity, runtime, AWSIM.

## 재현 명령

긴 명령과 세부 분석은 아래 문서에 접힘 블록으로 정리했습니다.

- [docs/PAPER_IMPLEMENTATION_REPORT.md](docs/PAPER_IMPLEMENTATION_REPORT.md)
- [docs/EXPERIMENT_STATUS.md](docs/EXPERIMENT_STATUS.md)
