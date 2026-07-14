# UTMR Paper Experiment Summary

이 문서는 논문 구현을 위해 처음부터 수행한 작업과 실험을 한눈에 보기 위한
요약표입니다.

## K64와 K256 차이

| 구분 | 뜻 | 논문에서 사용? | 이번 실험에서 왜 했나 | 결론 |
| --- | --- | --- | --- | --- |
| K64 | 한 planning step에서 후보 trajectory 64개를 평가 | 예. 현재 논문 구현의 주 설정 | 논문 설정을 맞추기 위해 K256 WoTE 공개 자산에서 K64 anchor/cache를 생성 | guarded UTMR가 full NAVSIM에서 baseline보다 높음 |
| K256 | 한 planning step에서 후보 trajectory 256개를 평가 | 아니오. WoTE 원본 공개 자산 기준 추가 검증 | 같은 guard가 더 강한 원본 WoTE K256 후보군에도 전이되는지 확인 | baseline이 훨씬 강하고 같은 guard는 약간 낮음. 별도 보수 guard는 subset에서 개선 |

즉, 논문 본문 결과로 볼 핵심은 K64입니다. K256은 논문 설정이 아니라
“원본 WoTE anchor에서도 같은 설정이 통하는가?”를 확인한 sanity/robustness
실험입니다.

## 전체 실험 타임라인

| # | 실험/작업 | 논문에서 의미 | 결과 양식 | 결과 요약 | 상태 |
| ---: | --- | --- | --- | --- | --- |
| 1 | UTMR 폴더 구조 분석 | Autoware/AWSIM/WoTE 실행 축을 분리 | 분석 메모 | offline NAVSIM/WoTE와 live AWSIM/Autoware 경로를 분리 | 분석 완료 |
| 2 | WoTE clone 확인 | 논문 planner backbone 확보 | git 상태 | `liyingyanUCAS/WoTE.git` 원본 기반 확인 | 확인 완료 |
| 3 | WoTE/NAVSIM 자산 준비 | 평가 실행 조건 확보 | asset check 표 | checkpoint, maps, logs, sensor blobs, metric cache 준비 | 준비 완료 |
| 4 | K64 anchor/cache 생성 | 논문 `K=64` 후보 설정 재현 | `.npy` 자산 | K256 공개 anchor/cache에서 K64 subset/cache 생성 | 생성 완료 |
| 5 | NAVSIM smoke/subset 실행 | pipeline sanity check | 로그/CSV | baseline/UTMR wrapper 실행 확인 | 실행 완료 |
| 6 | 초기 K64 full baseline | 논문 설정 baseline | 표 | `12146/12146`, score `0.8471632864` | 실행 완료 |
| 7 | 초기 K64 full UTMR | 첫 UTMR 구현 검증 | 표 + JSONL | score `0.8461780929`, 선택 변화 `0%` | 진단 완료 |
| 8 | 초기 UTMR 원인 분석 | 왜 효과가 없었는지 설명 | 코드/로그 분석 | `fine_scores=None`이라 실제 rerank가 안 됨 | 원인 확인 |
| 9 | WoTE UTMR reranking 수정 | coarse/fine selection 구현 | 코드 | fine score 연결, `rerank_accepted` logging 추가 | 구현 완료 |
| 10 | unguarded safety smoke | guard 없는 rerank 위험성 확인 | 표 | 선택 변화 `66%`, 점수 하락 경향 | 진단 완료 |
| 11 | K64 weight sweep 1000 | fine metric weight 영향 확인 | 표 | unguarded variants 대부분 baseline보다 낮음 | 실행 완료 |
| 12 | K64 guarded safety 1000 | selective rerank tuning | 표 | baseline `0.8638675087`, UTMR `0.8720460220` | 개선 확인 |
| 13 | K64 guarded safety full | 논문 설정 핵심 offline 결과 | 표 | baseline `0.8471632864`, UTMR `0.8542971577` | 개선 확인 |
| 14 | K64 full analysis | 논문 표/그림 재료 생성 | 표 + PNG | accepted `9.8139%`, runtime/selection figures 생성 | 산출물 생성 |
| 15 | K256 baseline full | WoTE 원본 anchor 기준선 | 표 | score `0.8833150351` | 실행 완료 |
| 16 | K256 guarded safety full | K64 guard의 K256 전이성 확인 | 표 | score `0.8827077445`, baseline보다 `-0.0006072906` | 개선 아님 |
| 17 | K64 guard sensitivity 1000 | guard parameter 근거 확보 | 표 + TSV | best `margin=0.15`, `drop=0.5`, `topN=8` | 근거 확보 |
| 18 | AWSIM/Autoware helper 구현 | live/closed-loop 실험 준비 | ROS 코드 | planner, object adapter, collision, metric monitor, batch runner 구현 | 구현 완료 |
| 19 | AWSIM initial live batch | live pipeline 첫 end-to-end smoke | episode CSV + runtime 표 | 초기 5 variants × 1 episode 실행, route success `0%` | 초기 smoke |
| 20 | AWSIM route/control smoke | Autoware route와 command gate 진단 | 로그 카운트 + CSV | route-missing/waiting은 `0`; command/heartbeat 경고는 감소했지만 일부 남음 | 진단 완료 |
| 21 | AWSIM dynamic TF smoke | sensor/localization TF mismatch 완화 | 로그 카운트 + CSV | `693` planner steps, observed metric row 생성. TF 경고 감소 | smoke evidence 확보 |
| 22 | AWSIM Odometry/service retry smoke | 실제 live localization topic과 service timing 진단 | runtime topic probe + episode CSV | `/localization/kinematic_state` publisher가 `nav_msgs/Odometry`; helper adapter 후 metric 숫자 기록 가능; localization `success=False`는 재시도 | blocker 일부 해결 |
| 23 | K256 retune 300/1000 | K256 별도 guard가 필요한지 확인 | PDM score 표 | 1000-scene에서 baseline `0.8852103916`, retuned UTMR `0.8900427692` | subset 개선 확인 |
| 24 | AWSIM stopped-condition/route fastpath | live localization과 route setup 안정화 | 코드 + smoke CSV | stop-check topic/threshold를 Autoware와 맞추고 synthetic route publisher를 기본 off | 구현/검증 완료 |
| 25 | AWSIM live batch fastpath | 현재 live closed-loop 비교 | episode CSV + Markdown tables + PNG figures | 5 variants x 1 episode, all success, collision source not measured, merged steps `4631` | closed-loop smoke 성공 |
| 26 | AWSIM repeated live batch with readiness wait | live closed-loop 반복성과 fallback 제거 확인 | episode CSV + Markdown tables + PNG figures | 5 variants x 5 episodes, all success, collision source not measured, fallback `0`, merged steps `32056` | 반복 실행 성공 |

## 핵심 결과 숫자

| 실험 | Baseline | UTMR | Delta | 논문에서의 의미 |
| --- | ---: | ---: | ---: | --- |
| K64 full NAVSIM | 0.8471632864 | 0.8542971577 | +0.0071338713 | 논문 설정에서 guarded UTMR 개선 확인 |
| K64 1000 best sensitivity | 0.8638675087 | 0.8720460220 | +0.0081785133 | 현재 guard 설정의 근거 |
| K256 full NAVSIM | 0.8833150351 | 0.8827077445 | -0.0006072906 | K256에는 별도 tuning 필요 |
| K256 retune 1000 | 0.8852103916 | 0.8900427692 | +0.0048323775 | 보수 guard는 K256 subset에서도 개선 |
| AWSIM live batch fastpath | 76.242342 | 76.269913 | +0.027571 | 1-episode smoke에서 UTMR가 baseline보다 근소하게 높음 |
| AWSIM repeated live batch | 75.991225 | 75.862994 | -0.128231 | 5-episode Shinjuku 반복에서는 full UTMR가 약간 낮고, `fine_dt_only`/`uniform_fine`이 근소하게 높음 |

## 결과물 양식

| 결과물 | 파일/형태 | 논문에서 쓰임 |
| --- | --- | --- |
| PDM score | CSV/log 기반 표 | main NAVSIM result table |
| `rerank_accepted_pct` | JSONL summary 표 | UTMR가 얼마나 개입했는지 설명 |
| latency/runtime | CSV/Markdown table | efficiency table |
| speed-uncertainty | PNG + CSV | figure 후보 |
| selection bias | PNG + CSV | figure 후보 |
| score landscape | PNG + CSV | qualitative figure 후보 |
| AWSIM episode metrics | CSV/표 | live integration 상태. 현재는 Shinjuku sample 1개 route의 `episodes=5` 반복 결과 |

## 지금 더 할 실험이 있나?

핵심 NAVSIM offline 실험은 끝났습니다.

남은 것은 “필수 구현”이라기보다 논문을 더 강하게 만드는 추가 검증입니다.

| 우선순위 | 남은 일 | 왜 필요한가 | 현재 상태 |
| ---: | --- | --- | --- |
| 1 | AWSIM scenario 다양화 | closed-loop/live benchmark 일반화 | 현재는 Shinjuku sample 1개 route x 5 episodes 기준 |
| 2 | K256 retuned full run | 원본 WoTE K256에서도 개선 여부 확인 | 1000-scene retuned guard는 개선 확인. full은 optional robustness check |
| 3 | 논문용 표/그림 polish | 제출용 결과 정리 | NAVSIM/AWSIM 표/그림 재료는 생성됨 |

따라서 지금 기준 결론은:

- NAVSIM/WoTE 논문 구현은 성공.
- K64 main result는 성공.
- K256 full에서 같은 guard는 낮았지만, 별도 보수 guard는 1000-scene subset에서 개선.
- AWSIM live path는 stopped-condition, service-order, route fastpath, readiness wait를 지나 5개 variant x 5 episodes 모두 observed success row를 만들었음. 최종 closed-loop benchmark는 scenario 다양화가 남음.
