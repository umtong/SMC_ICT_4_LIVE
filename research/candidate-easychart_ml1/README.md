# candidate-easychart_ml1

`candidate-easychart_re1_complete_bot_policy_v2`를 구조적 후보 생성기로 재사용하고,
숙련된 트레이더가 여러 맥락을 함께 보고 수행하는 **거래 품질 판단과 동시 후보
우선순위 결정**에만 ML을 사용합니다.

ML은 별도의 위험관리 계층이 아닙니다. 포지션 위험은 기존 계약 하나로 끝납니다.

- 시장 구조로 entry / stop / target을 진입 전에 확정
- 비용 전 계획 RR 1.0R 이상
- 손절 시 현재 NAV의 약 3% 손실이 되도록 수량 산정
- 모델 confidence에 따른 수량 축소 없음
- 별도의 일 손실 제한, exposure cap, 거래 quota 없음
- 한 번 진입하고 전량 stop 또는 전량 target으로 종료

## 의사결정 흐름

1. EasyChart/RE1 엔진이 유동성 사건과 구조 반응으로 완결된 causal plan을 만듭니다.
2. 계획 RR이 1.0R 미만이면 구조 단계에서 제외합니다.
3. ML은 당시 이용 가능한 구조, 거래량·공격자 흐름, 상위 방향, 네 종목 공통 움직임을
   함께 보고 `P(target before stop)`을 추정합니다.
4. 각 후보의 고유한 RR과 설정된 비용으로 post-cost 기대 R을 계산합니다.
5. 임의의 50%·60%·70% 확률선, probability edge, 목표 승률, coverage quota를
   덧붙이지 않습니다. 후보 자체의 비용 후 기대 R이 양수이면 거래 가능한 알파로 봅니다.
6. 같은 시각에 여러 후보가 발생하면 예상 R이 가장 높은 하나를 단일 계좌의 전역
   포지션 슬롯에 제출합니다.
7. 수량, 레버리지 사용, stop/target 주문, fill, fee와 continuous NAV는 기존
   RE1/NautilusTrader 실행층이 담당합니다. ML은 3% 위험을 줄이거나 늘리지 않습니다.

`기대 R > 0`은 추가적인 보수적 위험 제한이 아니라 모델이 발견하려는 알파의 최소
의미입니다. 음의 기대값을 양의 알파처럼 제출하지 않기 위한 자연스러운 경계이며,
그 위에 별도 안전 마진을 붙이지 않습니다.

## RE1에서 바꾼 책임 경계

RE1은 구조 후보 생성과 macro/common-factor boolean routing이 섞여 있었습니다.
ML1은 다음을 그대로 보존합니다.

- 유동성 사건, 구조 interaction, confirmation, first return/response
- OB/FVG/수평 S/R flip/추세선·채널/효율적 pullback의 mechanism ownership
- 같은 causal episode 중복 제거
- 진입 전 고정 entry/stop/target
- 비용 전 최소 1.0R

macro 정렬, 공통 impulse, 현재 flow, 변동성, 구조 강도와 geometry의 결합 품질은
확률 모델에 맡깁니다. 이는 위험 한도를 추가하는 작업이 아니라, 고정 boolean gate로
놓치던 좋은 거래와 허용하던 나쁜 거래를 더 잘 구분하기 위한 알파 연구입니다.

## 주요 파일

- `candidate_bundle_ml1.py`: 구조 후보 생성과 품질 판단 분리
- `ml1_features.py`: 심볼 ID가 없는 causal feature와 prior-only 상태
- `ml1_model.py`: portable JSON forest, probability calibration, 비용 후 R 계산
- `execution_ml1.py`: shadow/select와 기대 R 기반 arbitration
- `run_mtf_backtest_ml1.py`: 네 종목 단일 continuous NautilusTrader 실행
- `harvest_ml1.py`: 실행 종료 후 target-first/stop-first label 생성
- `build_ml1_dataset.py`: pre-entry feature와 future label 결합
- `train_ml1.py`: 시간순 train/calibration/test, label-horizon purge, Platt calibration
- `models/bootstrap_shadow.json`: 배선 및 데이터 수집용 모델

## Shadow 데이터 수집

```bash
python research/candidate-easychart_ml1/run_mtf_backtest_ml1.py \
  --start 2025-08-01 \
  --end 2025-09-30 \
  --warmup-days 30 \
  --symbols BTCUSDT ETHUSDT SOLUSDT XRPUSDT \
  --cache /path/to/cache \
  --output /path/to/ml1_shadow \
  --fee-profile usd_m_vip0 \
  --entry-slippage-ticks 2 \
  --stop-slippage-ticks 2 \
  --ml-mode shadow
```

Shadow는 기존 deterministic routing으로 주문하면서 넓은 후보 집합의 feature를
기록합니다. 모델 오류를 감추는 fallback이 아니라 학습 자료를 만드는 명시적 모드입니다.

## Label과 dataset

```bash
python research/candidate-easychart_ml1/harvest_ml1.py \
  --start 2025-08-01 \
  --end 2025-09-30 \
  --warmup-days 30 \
  --symbols BTCUSDT ETHUSDT SOLUSDT XRPUSDT \
  --cache /path/to/cache \
  --output /path/to/ml1_shadow \
  --fee-profile usd_m_vip0 \
  --entry-slippage-ticks 2 \
  --stop-slippage-ticks 2

python research/candidate-easychart_ml1/build_ml1_dataset.py \
  --run-output /path/to/ml1_shadow
```

미래 봉은 label 작성에만 사용되며 runtime feature로 들어가지 않습니다. 동일 1분봉에서
stop과 target이 모두 닿아 순서를 알 수 없는 경우는 데이터 해상도로 알 수 없는 사실을
승리로 꾸미지 않도록 stop-first로 기록합니다.

## 시간순 학습

```bash
python research/candidate-easychart_ml1/train_ml1.py \
  --dataset /path/to/ml1_shadow/ml1_dataset.csv \
  --model-output research/candidate-easychart_ml1/models/ml1_2025q3.json \
  --report-output /path/to/ml1_shadow/ml1_2025q3.report.json
```

학습·확률 보정·test 구간은 시간순으로 나뉘며 label interval이 다음 split을 넘는 row는
앞 구간에서 제거합니다. Calibration은 확률을 보정할 뿐, 70% 승률선이나 보수적
probability edge를 최적화하지 않습니다.

## ML select 실행

```bash
python research/candidate-easychart_ml1/run_mtf_backtest_ml1.py \
  --start 2025-10-01 \
  --end 2025-12-31 \
  --warmup-days 30 \
  --symbols BTCUSDT ETHUSDT SOLUSDT XRPUSDT \
  --cache /path/to/cache \
  --output /path/to/ml1_select \
  --fee-profile usd_m_vip0 \
  --entry-slippage-ticks 2 \
  --stop-slippage-ticks 2 \
  --ml-mode select \
  --ml-model research/candidate-easychart_ml1/models/ml1_2025q3.json
```

`select`는 trained artifact만 받습니다. 배선용 shadow model을 억지로 live selector처럼
허용하는 override나, 결과를 원하는 방향으로 바꾸는 probability threshold override는
두지 않습니다.

최종 성과는 counterfactual 후보 합계가 아니라 네 종목, 한 계좌, 한 전역 포지션,
실제 충돌 처리와 fixed-risk compounding을 적용한 하나의 continuous NAV로 확인합니다.
