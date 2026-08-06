# candidate-03 — NT-LVCFR-v1

**NautilusTrader Liquidity Vacuum Cascade / Failure Reversal**

이 브랜치의 활성 후보는 다른 연구 후보와 조립되지 않는 독립 시스템이다. 모든 공식 주간·장기 평가는 다음 단일 경로만 사용한다.

```text
checksum-verified Binance Vision data
→ ParquetDataCatalog
→ BacktestNode
→ NautilusTrader Strategy
→ native Risk/Execution Engine
→ native MarginAccount / Portfolio / funding / liquidation
→ after-cost account NAV
```

후보별 자체 체결 재생기, 포지션 회계, 수수료 계산기, NAV 시뮬레이터는 사용하지 않는다. 과거 FAR·LCPT·LVCFR 자체 재생 결과는 아이디어 이력일 뿐 공식 성공 근거가 아니다.

## 시장 구조 가설

첫 5분 displacement와 OI 감소는 반대 포지션의 축소가 시작됐음을 나타낼 수 있다. 다음 5분에도 가격과 OI 감소가 같은 방향으로 지속되지만 선물의 추가 공격 불균형은 작고 현물 흐름은 같은 방향을 지지한다면, 가격은 신규 시장가 주문의 크기보다 **반대편 유동성 철회·소진** 때문에 이동하는 상태일 수 있다.

```text
5m displacement + OI contraction
→ next 5m continuation + further OI contraction
→ spot alignment + low residual futures aggression
→ LIQUIDITY_VACUUM_CONFIRMED
→ one completed-minute entry buffer
    ├─ event extreme invalidated: no entry
    └─ valid: native FOK market entry
→ continuation target / structural protection / time exit
    └─ initial stop within 10m: one delayed failure reversal
```

동일 사건에서 지속과 반전을 미리 별도 전략으로 선택하지 않는다. 시장이 실제로 초기 극값을 지키는지 또는 빠르게 재점유하는지에 따라 상태가 전이한다. 반복 반전은 허용하지 않는다.

## 검출기와 시나리오 분리

`nt_lvcfr_data.py`는 완료된 선물·현물 1분봉과 5분 OI 관측만으로 사건을 확정하며 주문·체결·손익을 계산하지 않는다. `nt_lvcfr_strategy.py`는 확정 사건의 관측시각 이후 상태 전이와 주문 의사결정만 담당한다. 주문 생명주기, FOK 체결, 수수료, funding settlement, margin, liquidation, 포지션과 NAV는 NautilusTrader가 담당한다.

## 고정 중심 사양

| 구성요소 | 값 |
|---|---:|
| 최초 5분 displacement | 12bp 이상 |
| 두 번째 5분 가격 진행 | 같은 방향, 양수 |
| 두 구간 OI | 모두 감소 |
| 누적 OI 감소 | 10bp 이상 |
| 두 번째 활동량 | 이전 72개 5분 중앙값의 0.70배 이상 |
| 최소 활동량 이력 | 24개 5분 |
| 방향 정규화 선물 잔여 흐름 | 0.11 이하 |
| 방향 정규화 현물 흐름 | 0 이상 |
| ATR | 이전 완료 60분 |
| 초기 무효화 | 10분 사건 극값 ±0.20 ATR |
| 진입 버퍼 | 완성된 1분 1개 |
| 지속 목표 | 모든 예상 비용 후 3R |
| 보호 활성화 / 최소 확보 | 2R / 0.5R |
| 구조 추적 | 이전 완료 20분 극값 ±0.05 ATR |
| 지속 최대 보유 | 240분 |
| 빠른 실패 | 최초 진입 후 10분 이내 초기 손절 |
| 반전 | 다음 완성 분 경계 후 한 번만 |
| 반전 목표 / 최대 보유 | 1.5R / 180분 |
| 위험률 | 당시 native account equity의 3% |
| taker fee | 각 체결 5bp, instrument metadata |
| 슬리피지·시장충격 | 실제 top-of-book에 각 방향 1.5bp 불리하게 반영 |
| 유동성 | 실제 top size + L1 liquidity consumption + FOK 신규 진입 |
| funding | Binance Vision fundingRate → FundingRateUpdate → native settlement |
| liquidation | native maintenance-margin liquidation 활성화 |

수량은 다음 프로젝트 공식으로만 산정한다.

```text
계획 손실예산 = 현재 native account equity × 0.03

1개당 예상 손실 =
  |예상 진입 체결가 - 예상 손절 체결가|
  + 진입·손절 수수료
  + 예상 spread·slippage·market impact
  + 최대 보유 기준 불리한 funding

수량 = 계획 손실예산 ÷ 1개당 예상 손실
```

모델 점수, 방향, 종목, 최근 손익에 따른 위험배수나 별도 명목한도는 없다. 신규 진입은 FOK 한 건만 제출하며, 미체결 신규 진입 주문과 보유 포지션을 동시에 유지하지 않는다. 청산 주문은 reduce-only다.

## 데이터와 실행 현실성

- 공식 Binance Vision 파일과 `.CHECKSUM`을 검증한다.
- 선물 실제 `bookTicker`의 bid/ask와 top size를 사용한다.
- signal 관측 전 정보는 사용하지 않는다.
- signal이 존재할 수 있는 보유 구간은 원본 bookTicker 해상도를 유지한다.
- 그 외 구간은 분당 마지막 quote만 남겨 BacktestNode 반복을 빠르게 한다. 포지션이 열릴 수 있는 구간의 주문·손절·목표 판단은 축약하지 않는다.
- 시장 데이터는 `ParquetDataCatalog`에 기록하며, 전략은 `BacktestNode`에서 실행한다.

## 순차 BTC 게이트

선정 salt와 설정을 고정한 검증 순서는 다음과 같다.

1. `2024-01-08`
2. `2025-06-23`
3. `2022-05-16`

첫 주에 최소 8개 독립 episode, 승률 45%, 비용 후 양의 episode 기대값, NAV 일평균 기하성장률 1%, mark-to-market MDD 20% 미만을 만족해야 두 번째 주를 연다. 두 번째 주 통과 후에만 세 번째 주를 열고, 세 주 모두 통과해야 장기평가를 허용한다.

신호 수가 8개 미만이면 공식 성과 게이트를 달성할 수 없으므로, 주문·손익을 계산하지 않는 인과적 기회수 preflight에서 즉시 폐기한다. 신호 수가 충분한 경우 성과 판정은 오직 NautilusTrader 결과로 한다.

## 재현

사전 구축 환경에서는 설치하지 않는다.

```bash
smc4 doctor
PYTHONPATH=src:research/candidate-03 \
  python research/candidate-03/test_nt_lvcfr.py

PYTHONPATH=src:research/candidate-03 \
  python research/candidate-03/prepare_nt_lvcfr.py \
  --week-start 2024-01-08 \
  --output artifacts/candidate-03/nt-lvcfr/validation-1/prepared \
  --require-minimum

PYTHONPATH=src:research/candidate-03 \
  python research/candidate-03/run_nt_lvcfr.py \
  --week-start 2024-01-08 \
  --output artifacts/candidate-03/nt-lvcfr/validation-1

python research/candidate-03/gate_nt_lvcfr.py \
  artifacts/candidate-03/nt-lvcfr/validation-1/metrics.json \
  --config research/candidate-03/nt_lvcfr_config.json
```

## 알려진 실패 조건

- OI 감소가 실제 청산 연쇄가 아니라 포지션 교체·헤지 축소일 때.
- top-of-book 공백이 매우 짧아 FOK 수량이 체결되지 않을 때. 이는 성과가 아니라 용량 실패로 기록한다.
- 뉴스성 정보 주문이 유동성 공백을 만들고 초기 극값을 지킨 뒤 급반전할 때.
- Binance top-of-book만으로 다단계 호가 시장충격을 충분히 표현하지 못할 때. 1.5bp impact와 FOK top-size 제약으로 보수적으로 다룬다.
- 실제 거래소의 주문 지연이 이번 중심 가정보다 커서 진입·청산 가격이 더 나빠질 때.
- funding 급변이나 mark/index 괴리로 maintenance liquidation이 손절보다 먼저 발생할 때.

현재 브랜치의 성공 판정은 `metrics.json`의 엔진 값이 정확히 `NautilusTrader 1.230.0 BacktestNode`이고 순차 주간 게이트가 통과한 경우에만 유효하다.
