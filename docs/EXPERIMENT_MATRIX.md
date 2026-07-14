# UTMR Experiment Matrix

이 문서는 지금까지 논문 구현을 위해 수행한 실험을 한눈에 보기 위한 표입니다.

## K64와 K256

| 구분 | 의미 | 논문 설정 여부 | 이번 결과 해석 |
| --- | --- | --- | --- |
| K64 | 한 planning step에서 후보 trajectory 64개 평가 | 예. 논문 본문 설정 | UTMR guarded safety가 baseline보다 높아 핵심 성공 결과 |
| K256 | 후보 trajectory 256개 평가 | 아니오. WoTE 공개 원본 anchor 추가 검증 | baseline이 강하고 같은 guard UTMR는 약간 낮았지만, 별도 보수 guard는 subset에서 개선 |

## 전체 실험 표

| 순서 | 실험/작업 | 논문에서 의미 | 결과 양식 | 결과 요약 | 상태 |
| ---: | --- | --- | --- | --- | --- |
| 1 | UTMR 폴더 구조 분석 | Autoware/AWSIM/WoTE 실행 경로 파악 | 분석 메모 | NAVSIM/WoTE offline과 AWSIM/Autoware live 축으로 나눔 | 분석 완료 |
| 2 | WoTE clone 확인 | 논문 기반 world-model planner 확보 | 코드 상태 | `liyingyanUCAS/WoTE.git` 원본 기반 확인 | 확인 완료 |
| 3 | WoTE/NAVSIM 자산 준비 | 평가 실행 조건 확보 | asset check 표 | checkpoint, maps, logs, sensor blobs, metric cache 준비 | 준비 완료 |
| 4 | K64 anchor/cache 생성 | 논문 `K=64` 후보 설정 재현 | `.npy` asset | K256 공개 asset에서 K64 subset/cache 생성 | 생성 완료 |
| 5 | NAVSIM smoke/subset 실행 | 실행 파이프라인 sanity check | 로그 | baseline/UTMR wrapper 실행 확인 | 실행 완료 |
| 6 | 초기 K64 full baseline | 논문 설정 baseline | PDM score 표 | `12146/12146`, score `0.8471632864` | 실행 완료 |
| 7 | 초기 K64 full UTMR | 첫 UTMR 구현 검증 | PDM score 표 + step log | score `0.8461780929`, 선택 변화 `0%` | 진단 완료 |
| 8 | 초기 UTMR 원인 분석 | 왜 UTMR 효과가 없었는지 설명 | 코드/로그 분석 | `fine_scores=None` 때문에 실제 rerank가 안 됨 | 원인 확인 |
| 9 | WoTE UTMR reranking 수정 | coarse/fine reranking 구현 | 코드 | fine score를 selector에 연결, rerank 로그 추가 | 구현 완료 |
| 10 | unguarded safety smoke | guard 없는 rerank 위험성 확인 | PDM score 표 | 선택 변화 `66%`, score 하락 경향 | 진단 완료 |
| 11 | K64 weight sweep 1000 | fine metric weight 영향 확인 | PDM score 표 | unguarded variants 대부분 baseline보다 낮음 | 실행 완료 |
| 12 | K64 guarded safety 1000 | selective rerank tuning | PDM score 표 | baseline `0.8638675087`, UTMR `0.8720460220` | 개선 확인 |
| 13 | K64 guarded safety full | 논문 설정 핵심 offline 결과 | PDM score 표 | baseline `0.8471632864`, UTMR `0.8542971577` | 개선 확인 |
| 14 | K64 full analysis | 논문 그림/표 재료 | 표 + PNG figure | rerank accepted `9.8139%`, analysis 산출물 생성 | 산출물 생성 |
| 15 | K256 baseline full | 원본 WoTE anchor 기준선 | PDM score 표 | score `0.8833150351` | 실행 완료 |
| 16 | K256 guarded safety full | K64 guard의 K256 전이성 확인 | PDM score 표 | score `0.8827077445`, baseline보다 `-0.0006072906` | 개선 아님 |
| 17 | K64 guard sensitivity 1000 | guard parameter 근거 확보 | PDM score 표 + summary TSV | best `margin=0.15`, `drop=0.5`, `topN=8` | 근거 확보 |
| 18 | AWSIM/Autoware helper 구현 | closed-loop/live 실험 준비 | ROS/helper 코드 | planner publisher, collision monitor, metric monitor, batch runner 구현 | 구현 완료 |
| 19 | AWSIM/Autoware live batch smoke | closed-loop/live pipeline 검증 | episode CSV + runtime 표 | 초기 5 variants x 1 episode 실행, route success `0%`, collision source not measured, merged steps `1125` | 초기 smoke |
| 20 | AWSIM route/control smoke | route와 command gate 병목 진단 | 로그 카운트 + CSV | route missing/waiting은 `0`; command/heartbeat 경고는 감소했지만 일부 남음 | 진단 완료 |
| 21 | AWSIM dynamic TF smoke | sensor/localization TF mismatch 완화 | 로그 카운트 + CSV | observed metric row, `693` planner steps. route success는 아직 `0%` | smoke evidence 확보 |
| 22 | AWSIM Odometry/service retry smoke | live helper가 실제 localization topic과 service 응답을 받는지 검증 | runtime topic probe + CSV | `/localization/kinematic_state` publisher가 `nav_msgs/Odometry`; helper adapter 후 metric 숫자 기록 가능; localization `success=False`는 재시도 | blocker 일부 해결 |
| 23 | K256 retune 300 | K256 별도 guard 후보 탐색 | PDM score 표 | baseline `0.9022969937`, best 후보 `0.9034554556`; 보수 후보 `m0.20/drop0.2/topN4`도 `0.9033675968` | 후보 확보 |
| 24 | K256 retune 1000 | K256 보수 후보 확대 검증 | PDM score 표 + step summary | baseline `0.8852103916`, `m0.20/drop0.2/topN4` UTMR `0.8900427692`, accepted `3.0%` | subset 개선 확인 |
| 25 | AWSIM stopped-condition fix | localization init 실패 원인 제거 | Autoware source trace + live smoke | stop-check topic을 `/sensing/vehicle_velocity_converter/twist_with_covariance`, threshold `0.001m/s`, hold `3s`로 맞춤 | 구현/검증 완료 |
| 26 | AWSIM route fastpath smoke | stale route/empty route publisher 제거 | episode CSV + log check | synthetic route publisher 기본 off, planning clear/waypoint 기본 off, `UTMR_READY=1`, success observed | smoke 성공 |
| 27 | AWSIM live batch fastpath | 현재 live closed-loop 비교 | table_i/table_ii/table_iii + figures | 5 variants x 1 episode, all success, collision source not measured, merged steps `4631` | closed-loop smoke 성공 |
| 28 | AWSIM repeated live batch with readiness wait | live closed-loop 반복성과 fallback 제거 확인 | episode CSV + Markdown tables + PNG figures | 5 variants x 5 episodes, all success, collision source not measured, fallback `0`, merged steps `32056` | 반복 실행 성공 |

## 핵심 숫자

| 실험 | Baseline | UTMR | Delta | 결론 |
| --- | ---: | ---: | ---: | --- |
| K64 full | 0.8471632864 | 0.8542971577 | +0.0071338713 | 논문 설정에서 UTMR 개선 |
| K64 1000 best sensitivity | 0.8638675087 | 0.8720460220 | +0.0081785133 | 현재 guard 설정 근거 |
| K256 full | 0.8833150351 | 0.8827077445 | -0.0006072906 | K256은 별도 tuning 필요 |
| K256 retune 1000 | 0.8852103916 | 0.8900427692 | +0.0048323775 | 보수 guard는 K256 subset에서도 개선 |
| AWSIM live batch fastpath | 76.242342 | 76.269913 | +0.027571 | 1-episode smoke에서는 UTMR가 baseline보다 근소하게 높음 |
| AWSIM repeated live batch | 75.991225 | 75.862994 | -0.128231 | 5-episode Shinjuku 반복에서는 full UTMR가 약간 낮고, `fine_dt_only`/`uniform_fine`이 근소하게 높음 |

## 결과물 양식

| 결과물 | 파일/형태 | 논문에서 쓰임 |
| --- | --- | --- |
| PDM score | CSV/log 기반 표 | main result table |
| rerank accepted / selected changed | JSONL summary 표 | UTMR 개입률 설명 |
| latency/runtime | summary TSV 표 | computational efficiency table |
| speed-uncertainty | PNG + CSV | uncertainty figure |
| selection bias | PNG + CSV | selection behavior figure |
| score landscape | PNG + CSV | qualitative figure |
| AWSIM episode metrics | CSV/표 | live integration table. 현재는 Shinjuku sample 1개 route의 `episodes=5` 반복 결과 |

## 현재 결론

1. 논문 설정인 `K=64`에서는 guarded safety UTMR가 full NAVSIM에서 baseline보다 높습니다.
2. 같은 guard를 `K=256`에 그대로 적용하면 baseline보다 약간 낮지만, 별도 보수 guard(`margin=0.20`, `drop=0.2`, `topN=4`)는 1000-scene subset에서 baseline보다 높았습니다.
3. K64 sensitivity는 현재 best 설정 `margin=0.15`, `drop=0.5`, `topN=8`을 뒷받침합니다.
4. AWSIM/Autoware live path는 route/control/TF/Odometry/stopped-condition 문제를 지나 5개 variant x 5 episodes 모두 observed success row를 만들었습니다.
5. AWSIM 결과는 이제 fallback 없는 반복 실행까지 됐지만, 아직 Shinjuku sample 1개 route라 최종 closed-loop benchmark로 쓰려면 scenario/route 다양화가 필요합니다.
