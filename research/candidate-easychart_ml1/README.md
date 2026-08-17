# candidate-easychart_ml1

`candidate-easychart_re1_complete_bot_policy_v2`를 구조적 후보 생성기로 재사용하고,
고정 boolean 규칙으로 다루기 어려운 **거래 맥락의 결합 판단**에 ML을 사용하는
연구 브랜치입니다.

## 바뀌지 않는 거래 계약

- BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT 공통 핵심 로직
- 단일 계좌, 네 종목 전체 하나의 전역 포지션
- 시장 구조로 entry / stop / target을 진입 전에 확정
- 비용 전 계획 RR 1.0R 이상
- 손절 시 현재 NAV의 약 3% 손실이 되도록 수량과 필요한 exposure 산정
- 모델 confidence에 따른 수량 축소 없음
- 별도의 일 손실 제한, exposure cap, 거래 quota 없음
- 한 번 진입하고 전량 stop 또는 전량 target으로 종료
- 주문·체결·수수료·NAV accounting은 기존 RE1/NautilusTrader 실행층 사용

## ML이 맡는 일

EasyChart/RE1 엔진이 먼저 유동성 사건, 구조 반응, confirmation, 진입, 무효화점과
현실적인 목표를 가진 완결된 plan을 만듭니다. ML은 당시 이용 가능한 구조·거래량·
공격자 흐름·상위 방향·네 종목 공통 움직임을 함께 보고
`P(target before stop)`을 추정합니다.

**승률 70%는 학습 목적함수도 runtime threshold도 아닙니다.** 숙련된 트레이더와
유사한 좋은 시스템에서 기대되는 결과의 모습이지, 숫자를 맞추기 위해 후보를 자르는
규칙이 아닙니다. 실제 승률·거래빈도·RR·NAV 경로는 시장 논리와 거래 선택이 제대로
작동할 때 결과로 나타나야 합니다.

반대로 RR이 크다는 이유만으로 낮은 성공 가능성을 허용하지도 않습니다. 모든 후보가
이미 RR 1R 이상이므로 runtime은 다음 두 가지를 봅니다.

1. 해당 plan의 target이 stop보다 먼저 도달할 가능성이 더 높은가?
2. 설정된 비용까지 반영해 기대값이 양수인가?

따라서 4R plan의 추정 성공확률이 40%라면 산술 기대 R이 양수여도 선택하지 않습니다.
1R plan의 추정 성공확률이 60%이고 비용 후에도 양수라면 선택할 수 있습니다. 이는
70%를 맞추는 최적화가 아니라, **실패할 가능성이 더 높은 계획을 큰 목표로 정당화하지
않는 거래 품질 판단**입니다.

동시에 여러 후보가 나오면 target-first 확률이 높은 후보를 먼저 선택하고, 확률이
같거나 매우 가까운 경우에만 비용 후 기대 R과 RR을 다음 순서로 사용합니다. 승률을
희생해 고R을 사는 구조가 아닙니다.

## 후보 생성과 학습 자료

ML1은 구조적 geometry를 느슨하게 하지 않습니다. 다만 RE1의 macro/common-factor
boolean gate가 미리 숨기던 완결 후보도 기록하여, 실제로 좋은 누락인지 나쁜 후보인지
future first-passage 결과로 확인합니다. 같은 causal episode의 반복 신호는 하나로
관리합니다.

`MatureDiagonalResponseFamily`는 원래 설계된 15m/5m/1m 봉만 소비합니다. 60m 봉은
다른 family의 context이며 이 micro engine에 전달하지 않습니다.

## Shadow 실행

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

Shadow에서는 기존 deterministic routing으로 거래하면서 넓은 후보 집합의 pre-entry
feature를 기록합니다. bootstrap model은 배선·데이터 수집용이며 select mode에 사용할
성과 모델이 아닙니다.

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

미래 봉은 실행이 끝난 뒤 target-first / stop-first label을 작성하는 데만 사용됩니다.
runtime feature에는 들어가지 않습니다.

## 시간순 학습

```bash
python research/candidate-easychart_ml1/train_ml1.py \
  --dataset /path/to/ml1_shadow/ml1_dataset.csv \
  --model-output research/candidate-easychart_ml1/models/ml1_2025q3.json \
  --report-output /path/to/ml1_shadow/ml1_2025q3.report.json
```

학습·확률 보정·test 구간은 시간순으로 나뉩니다. label interval이 다음 split을 넘는
row는 앞 구간에서 제거합니다. Calibration은 확률의 의미를 바로잡을 뿐 목표 승률을
맞추는 threshold를 찾지 않습니다.

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

훈련되지 않은 `shadow_only` model은 select mode에서 사용하지 않습니다. 모델 또는
feature schema 오류를 조용히 fallback으로 감추지 않고 구현 오류로 드러냅니다.

실제 선택 결과의 승률이 낮다면 70% threshold를 올려 숫자를 꾸미지 않습니다. 어떤
시장 상태·scenario·entry geometry를 잘못 이해했는지 후보와 체결을 대조해 고칩니다.
