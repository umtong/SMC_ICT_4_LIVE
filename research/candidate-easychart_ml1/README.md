# candidate-easychart_ml1

`candidate-easychart_re1_complete_bot_policy_v2`를 **구조적 후보 생성기**로 재사용하고,
사람 트레이더가 자연스럽게 수행하지만 고정 임계값으로 번역하기 어려웠던
**상황 선별과 동시 후보 선택**만 ML로 해결하는 연구 브랜치입니다.

핵심은 가격 방향을 무작정 예측하는 모델이 아닙니다.

1. 기존 EasyChart/RE1 엔진이 완결된 causal episode를 찾습니다.
2. 진입가·손절가·목표가와 비용 전 `RR >= 1.0R`을 진입 전에 고정합니다.
3. ML은 당시 이용 가능한 구조·거래량/공격자 흐름·상위 방향·4종목 공통 움직임을
   함께 보고 `P(target before stop)`을 추정합니다.
4. 수수료·슬리피지·펀딩을 R 단위로 환산해 비용 후 기대값이 양수인 후보만
   선택하거나 모두 거절합니다.
5. 동시에 여러 종목/시나리오가 나오면 비용 후 기대 R가 가장 높은 하나만
   단일 계좌의 전역 포지션 슬롯에 제출합니다.
6. 주문·체결·3% 고정 NAV 위험수량·보호주문·continuous account accounting은
   검증된 RE1/NautilusTrader 실행층을 그대로 사용합니다.

## RE1에서 바꾼 책임 경계

RE1은 후보를 만드는 구조 로직과 후보를 고르는 macro/common-factor boolean gate가
섞여 있었습니다. gate가 거절한 예시는 학습 자료에도 나타나지 않으므로, 임계값을
계속 손보는 연구가 되기 쉬웠습니다.

ML1의 `EasyChartML1CandidateBundle`은 다음을 유지합니다.

- 유동성 사건, 구조 interaction, confirmation, first return/response
- OB/FVG/수평 S/R flip/추세선·채널/효율적 pullback 등 mechanism ownership
- 같은 causal episode 중복 제거
- 진입 전 고정 entry/stop/target
- 비용 전 최소 1.0R

반면 macro 정렬, 시장 공통 impulse, 현재 흐름, 변동성, 구조 강도와 geometry의
**결합된 품질 판단**은 selector로 넘깁니다. RE1 market factor는 여전히 causal
feature로 계산되지만 후보 엔진을 미리 차단하지 않습니다.

## 파일

- `candidate_bundle_ml1.py`: 구조 후보 생성과 품질 선택 분리
- `ml1_features.py`: 심볼 ID가 없는 118개 causal feature와 prior-only 상태
- `ml1_model.py`: 표준 라이브러리만 사용하는 portable JSON forest, calibration,
  비용 후 R 계산
- `execution_ml1.py`: shadow/select, abstention, 기대 R arbitration, RE1 실행 재사용
- `run_mtf_backtest_ml1.py`: 네 종목 단일 continuous NautilusTrader 실행
- `harvest_ml1.py`: 실행 종료 후 future first-passage 정답지 생성
- `build_ml1_dataset.py`: pre-entry feature와 post-run label의 identity-safe 결합
- `train_ml1.py`: 시간순 train/calibration/test, label-horizon purge, Platt calibration,
  pure-Python JSON export
- `models/bootstrap_shadow.json`: 배선 확인 전용. **select 모드용 성과 모델이 아님**

## 1. Shadow 실행과 실제 후보 수집

기존 RE1에 가까운 deterministic routing으로 거래하면서, 더 넓은 후보 집합의
ML feature를 모두 기록합니다. bootstrap 모델은 0.5를 반환할 뿐이고 거래 결정을
바꾸지 않습니다.

```bash
python research/candidate-easychart_ml1/run_mtf_backtest_ml1.py \
  --start 2025-08-01 \
  --end 2025-09-30 \
  --warmup-days 30 \
  --symbols BTCUSDT ETHUSDT SOLUSDT XRPUSDT \
  --cache /path/to/cache \
  --output /path/to/ml1_shadow \
  --fee-profile binance_usdm_vip0 \
  --entry-slippage-ticks 2 \
  --stop-slippage-ticks 2 \
  --ml-mode shadow
```

`decision_events.csv`에는 기존 `kind=plan`과 함께 각 후보의 `kind=ml_plan`,
`mlf_*` feature, 추정 비용, baseline eligibility가 남습니다.

## 2. Future label은 실행 종료 후에만 생성

```bash
python research/candidate-easychart_ml1/harvest_ml1.py \
  --start 2025-08-01 \
  --end 2025-09-30 \
  --warmup-days 30 \
  --symbols BTCUSDT ETHUSDT SOLUSDT XRPUSDT \
  --cache /path/to/cache \
  --output /path/to/ml1_shadow \
  --fee-profile binance_usdm_vip0 \
  --entry-slippage-ticks 2 \
  --stop-slippage-ticks 2
```

동일 1분봉에서 stop과 target이 모두 닿으면 보수적으로 손실 label입니다.
이 단계의 미래 데이터는 연구 정답지일 뿐 전략 코드에 import되지 않습니다.

## 3. Dataset 생성

```bash
python research/candidate-easychart_ml1/build_ml1_dataset.py \
  --run-output /path/to/ml1_shadow
```

결과:

- `ml1_dataset.csv`
- `ml1_dataset_summary.json`

`symbol`은 자산별 진단을 위해 파일에 남지만 `FEATURE_NAMES`에는 들어가지 않으며
모델 입력에도 사용하지 않습니다.

## 4. 시간순 학습·확률 보정·portable model export

```bash
python research/candidate-easychart_ml1/train_ml1.py \
  --dataset /path/to/ml1_shadow/ml1_dataset.csv \
  --model-output research/candidate-easychart_ml1/models/ml1_2025q3.json \
  --report-output /path/to/ml1_shadow/ml1_2025q3.report.json
```

학습 구간, probability calibration 구간, test 구간은 시간순으로 분리됩니다.
각 plan의 결과가 다음 구간까지 걸쳐 있으면 그 plan은 앞 구간에서 제거됩니다.
동일 시각에 나온 여러 후보는 합계 weight가 1이 되도록 가중해 하나의 cascade가
학습을 지배하지 않게 합니다. calibration data는 forest fitting에 사용하지 않습니다.

보고서의 counterfactual 선택 성과는 모델 진단입니다. 최종 성과는 다음 단계의
**네 종목·단일 전역 슬롯·continuous NAV** 실행 결과로 판단해야 합니다.

## 5. ML select 실행

```bash
python research/candidate-easychart_ml1/run_mtf_backtest_ml1.py \
  --start 2025-10-01 \
  --end 2025-12-31 \
  --warmup-days 30 \
  --symbols BTCUSDT ETHUSDT SOLUSDT XRPUSDT \
  --cache /path/to/cache \
  --output /path/to/ml1_select \
  --fee-profile binance_usdm_vip0 \
  --entry-slippage-ticks 2 \
  --stop-slippage-ticks 2 \
  --ml-mode select \
  --ml-model research/candidate-easychart_ml1/models/ml1_2025q3.json
```

훈련되지 않은 `shadow_only` 모델은 select 모드에서 거부됩니다. 연구상 임시 배선
검사 외에는 `--ml-allow-shadow-model`을 쓰지 않습니다.

모델 파일의 calibration 구간에서 선택된 probability floor와 break-even probability
edge가 기본값입니다. 진단 목적으로만 다음 값을 override할 수 있습니다.

```text
--ml-min-probability
--ml-probability-edge
--ml-min-expected-net-r
--ml-target-slippage-ticks
```

## 지켜지는 운용 계약

- BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT 공통 핵심 로직
- 단일 계좌, 전 종목 하나의 전역 포지션
- NAV의 3% 고정 손실 위험수량
- 전액 포지션 한 번 진입, 한 번 청산; 분할 없음
- 진입 전 entry/stop/target 고정
- 비용 전 계획 RR 1.0R 이상
- 하루 손실 제한 및 인위적 거래 수 제한 없음
- stop 또는 target의 최초 완결로 청산
- NautilusTrader 주문·체결·수수료·portfolio accounting 재사용

## 현재 저장소 모델 상태

커밋된 `bootstrap_shadow.json`은 코드와 이벤트 파이프라인을 검증하기 위한 모델입니다.
실제 RE1 후보 label 데이터가 이 작업 환경에 없었으므로 성과를 가장한 fitted model은
커밋하지 않습니다. 실제 shadow 결과로 학습한 artifact와 별도 기간 select 결과가
생겨야 장기 continuous/paper 후보를 판단할 수 있습니다.
