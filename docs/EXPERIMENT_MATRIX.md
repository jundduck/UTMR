# UTMR Experiment Matrix

이 문서는 지금까지 논문 구현을 위해 수행한 실험을 한눈에 보기 위한 표입니다.

## K64와 K256

| 구분 | 의미 | 논문 설정 여부 | 이번 결과 해석 |
| --- | --- | --- | --- |
| K64 | 한 planning step에서 후보 trajectory 64개 평가 | 예. 논문 본문 설정 | UTMR guarded safety가 baseline보다 높아 핵심 성공 결과 |
| K256 | 후보 trajectory 256개 평가 | 아니오. WoTE 공개 원본 anchor 추가 검증 | baseline이 강하고 같은 guard UTMR는 약간 낮아 별도 tuning 필요 |

## 전체 실험 표

| 순서 | 실험/작업 | 논문에서 의미 | 결과 양식 | 결과 요약 | 성공 유무 |
| ---: | --- | --- | --- | --- | --- |
| 1 | UTMR 폴더 구조 분석 | Autoware/AWSIM/WoTE 실행 경로 파악 | 분석 메모 | NAVSIM/WoTE offline과 AWSIM/Autoware live 축으로 나눔 | 성공 |
| 2 | WoTE clone 확인 | 논문 기반 world-model planner 확보 | 코드 상태 | `liyingyanUCAS/WoTE.git` 원본 기반 확인 | 성공 |
| 3 | WoTE/NAVSIM 자산 준비 | 평가 실행 조건 확보 | asset check 표 | checkpoint, maps, logs, sensor blobs, metric cache 준비 | 성공 |
| 4 | K64 anchor/cache 생성 | 논문 `K=64` 후보 설정 재현 | `.npy` asset | K256 공개 asset에서 K64 subset/cache 생성 | 성공 |
| 5 | NAVSIM smoke/subset 실행 | 실행 파이프라인 sanity check | 로그 | baseline/UTMR wrapper 실행 확인 | 성공 |
| 6 | 초기 K64 full baseline | 논문 설정 baseline | PDM score 표 | `12146/12146`, score `0.8471632864` | 성공 |
| 7 | 초기 K64 full UTMR | 첫 UTMR 구현 검증 | PDM score 표 + step log | score `0.8461780929`, 선택 변화 `0%` | 진단 성공 |
| 8 | 초기 UTMR 원인 분석 | 왜 UTMR 효과가 없었는지 설명 | 코드/로그 분석 | `fine_scores=None` 때문에 실제 rerank가 안 됨 | 성공 |
| 9 | WoTE UTMR reranking 수정 | coarse/fine reranking 구현 | 코드 | fine score를 selector에 연결, rerank 로그 추가 | 성공 |
| 10 | unguarded safety smoke | guard 없는 rerank 위험성 확인 | PDM score 표 | 선택 변화 `66%`, score 하락 경향 | 성공 |
| 11 | K64 weight sweep 1000 | fine metric weight 영향 확인 | PDM score 표 | unguarded variants 대부분 baseline보다 낮음 | 성공 |
| 12 | K64 guarded safety 1000 | selective rerank tuning | PDM score 표 | baseline `0.8638675087`, UTMR `0.8720460220` | 성공 |
| 13 | K64 guarded safety full | 논문 설정 핵심 offline 결과 | PDM score 표 | baseline `0.8471632864`, UTMR `0.8542971577` | 성공 |
| 14 | K64 full analysis | 논문 그림/표 재료 | 표 + PNG figure | rerank accepted `9.8139%`, analysis 산출물 생성 | 성공 |
| 15 | K256 baseline full | 원본 WoTE anchor 기준선 | PDM score 표 | score `0.8833150351` | 성공 |
| 16 | K256 guarded safety full | K64 효과와 원본 anchor 효과 분리 | PDM score 표 | score `0.8827077445`, baseline보다 `-0.0006072906` | 추가 검증 성공, 개선은 아님 |
| 17 | K64 guard sensitivity 1000 | guard parameter 근거 확보 | PDM score 표 + summary TSV | best `margin=0.15`, `drop=0.5`, `topN=8` | 성공 |
| 18 | AWSIM/Autoware helper 구현 | closed-loop/live 실험 준비 | ROS/helper 코드 | planner publisher, collision monitor, metric monitor, batch runner 구현 | 코드 성공 |
| 19 | AWSIM/Autoware live batch | closed-loop benchmark | episode CSV/표 예정 | 실제 simulator topic 연결 후 실행 필요 | 미실행 |

## 핵심 숫자

| 실험 | Baseline | UTMR | Delta | 결론 |
| --- | ---: | ---: | ---: | --- |
| K64 full | 0.8471632864 | 0.8542971577 | +0.0071338713 | 논문 설정에서 UTMR 개선 |
| K64 1000 best sensitivity | 0.8638675087 | 0.8720460220 | +0.0081785132 | 현재 guard 설정 근거 |
| K256 full | 0.8833150351 | 0.8827077445 | -0.0006072906 | K256은 별도 tuning 필요 |

## 결과물 양식

| 결과물 | 파일/형태 | 논문에서 쓰임 |
| --- | --- | --- |
| PDM score | CSV/log 기반 표 | main result table |
| rerank accepted / selected changed | JSONL summary 표 | UTMR 개입률 설명 |
| latency/runtime | summary TSV 표 | computational efficiency table |
| speed-uncertainty | PNG + CSV | uncertainty figure |
| selection bias | PNG + CSV | selection behavior figure |
| score landscape | PNG + CSV | qualitative figure |
| AWSIM episode metrics | CSV/표 예정 | closed-loop benchmark table |

## 현재 결론

1. 논문 설정인 `K=64`에서는 guarded safety UTMR가 full NAVSIM에서 baseline보다 높습니다.
2. 같은 guard를 `K=256`에 그대로 적용하면 baseline보다 약간 낮습니다.
3. K64 sensitivity는 현재 best 설정 `margin=0.15`, `drop=0.5`, `topN=8`을 뒷받침합니다.
4. 남은 큰 실험은 AWSIM/Autoware live batch입니다.
