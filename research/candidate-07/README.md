# Candidate 07 — Causal Liquidity Shock Router (CLSR)

`candidate-07`은 독립적인 BTC-first SMC/ICT 데이트레이딩 후보다. 단순 sweep, FVG, BOS 또는 큰 캔들을 진입 패턴으로 사용하지 않고, 이미 형성된 유동성에 가격이 접촉한 뒤 나타나는 두 개의 상반된 경매 결과를 상태 전이로 분기한다.

```text
과거 데이터만으로 형성된 외부 유동성
        -> 접촉/침투
        -> A. 유동성 밖 가격이 거부되고 range 안으로 회수됨
               -> 반대 방향 displacement/MSS 확인
               -> absorption-reclaim reversal
        -> B. 유동성 밖에서 displacement가 종가로 수용됨
               -> 다음 신호봉이 바깥 가격을 유지
               -> acceptance-continuation
        -> 다음 1분봉 종가에서 NautilusTrader bracket 주문
        -> 구조 손절 / 유동성 또는 측정 확장 목표 / 시간 청산
```

## 왜 이 후보인가

저장소 공통 기반과 관련 후보를 먼저 확인했다.

- 공통 기반은 주문, 포지션, 계좌, 수수료, 펀딩, 이벤트 재생을 NautilusTrader에 맡기도록 명시한다.
- candidate-03은 OI·aggressor-flow 기반의 강한 개발 성과를 보였지만 별도 포트폴리오 시뮬레이션 경로를 사용했다.
- candidate-04는 sweep 뒤 rejection/acceptance 분기에서 강한 한 주 결과를 보였지만 역시 빠른 자체 시뮬레이터를 먼저 사용했다.

candidate-07은 이 연구적 교훈만 취하고, 첫 주간 반복부터 장기평가까지 **동일한 NautilusTrader `BacktestEngine` 주문·계좌 경로만** 사용한다. `model.py`는 시장 상태를 판정할 뿐 체결, PnL, 현금, 포지션 또는 백테스트를 계산하지 않는다.

## 기계적 정의

### 외부 유동성

현재 신호봉을 제외한 직전 `external_lookback`개의 5분 집계봉 최고가·최저가다. 현재 봉을 포함하지 않으므로 돌파봉이 자신의 유동성 수준을 생성할 수 없다.

### Sweep / absorption

외부 수준을 최소 ATR 비율만큼 침투했지만 종가가 range 안으로 회수되고, 꼬리 비율과 과거 거래량 분포 대비 활동 증가가 함께 확인된 사건이다. sweep만으로 진입하지 않는다.

### Acceptance / displacement

외부 수준 밖 종가, ATR 대비 몸통, 종가 위치, 거래량 상태, 방향성 효율이 동시에 확인된 사건이다. 다음 신호봉이 수준 밖을 유지해야 continuation으로 인정한다.

### MSS confirmation

sweep 뒤 반대 방향 몸통과 수준 회수가 후속 완료봉에서 확인된 상태다. 시각적 pivot을 미래 봉으로 소급 표시하지 않으며, 알고리즘 관측 시각은 항상 확인봉 종료 시각이다.

### 진입·무효화·목표

- 확인 후 첫 번째 완성 1분봉 종가에서 시장가 parent를 제출한다.
- stop-market과 take-profit limit을 OUO bracket child로 제출한다.
- reversal 목표는 사건 이전부터 알려진 반대편 내부/외부 유동성 중 가장 가까운 경제적으로 유효한 수준이다.
- continuation 목표는 수용된 경매의 위험 단위 측정 확장이다.
- 다음 유동성이 비용 후 최소 R을 제공하지 않거나 1분 지연으로 geometry가 훼손되면 진입하지 않는다.

## 위험과 비용

수량은 매 거래 시점의 NautilusTrader 전체 계좌 NAV를 사용한다.

```text
계획 손실예산 = current NAV × 0.03

1개당 예상 손실
= |예상 진입 체결가 - 예상 손절 체결가|
+ 진입 taker fee
+ 손절 taker fee
+ 진입·손절 각각 1 adverse tick
+ adverse funding reserve

수량 = 계획 손실예산 / 1개당 예상 손실
```

별도 명목한도, 전략 점수 위험배수, 종목별 위험배수는 없다. Venue의 실제 수량 정밀도만 적용한다. NETTING OMS와 전략 상태가 신규 pending entry 또는 포지션 하나만 허용한다.

## 데이터와 실행

- Binance Vision USD-M `BTCUSDT` 1분 kline
- Binance Vision historical funding-rate archive
- 모든 archive의 공식 `.CHECKSUM` 검증
- NautilusTrader `CryptoPerpetual` instrument (`BTCUSDT-PERP.BINANCE`)
- NautilusTrader `MakerTakerFeeModel`
- bar-data fill model: 모든 taker fill에 one-tick adverse slippage
- historical `FundingRateUpdate`를 동일 engine stream에 추가
- 동일 1분봉 안에서 stop과 target이 모두 가능한 경우 NautilusTrader의 bar matching 순서가 판정

1분 kline은 실제 L2 refill 또는 queue depletion을 직접 보여주지 않는다. 따라서 현재 버전의 absorption은 wick·reclaim·volume을 이용한 실행 가능한 proxy이며, 목표 통과 후 더 세밀한 데이터로 대체 검증할 항목이다.

## 사전 고정 무작위 BTC 주간

시장 데이터를 열기 전에 `random.Random(7007)`로 주간을 고정했고, 시작일 사이 최소 28일을 두었다.

| 단계 | UTC 구간 | 용도 |
|---|---|---|
| week-1 | 2025-12-22 ~ 2025-12-28 | 개발·인과 오류 분리 |
| week-2 | 2025-01-27 ~ 2025-02-02 | 동결 전진검증 |
| week-3 | 2024-06-24 ~ 2024-06-30 | 동결 전진검증 |

week-1이 구조적 가능성을 보이지 않으면 week-2를 열지 않는다. 수정할 때는 하나의 원인군만 바꾸고 week-1을 NautilusTrader로 다시 실행한다. 세 주간이 모두 통과할 때만 `2024-01-01`부터 `2025-12-31`까지 장기평가를 연다.

주간 gate:

- 비용·펀딩 후 NAV 일평균 기하성장률 `>= 1%`
- 거래 `>= 7`
- 거래 활동일 `>= 4`
- 최대낙폭 `<= 20%`
- 단일 최대 승리의 gross-profit 비중 `<= 55%`
- 양의 NAV 및 단일 slot 불변식

## 재현

사전 구축 Codespace/Dev Container에서 설치하지 않는다.

```bash
smc4 doctor
python -m unittest discover \
  -s research/candidate-07/tests \
  -p 'test_*.py' -v

python research/candidate-07/candidate.py pipeline \
  --config research/candidate-07/config.json \
  --week-plan research/candidate-07/week_plan.json \
  --output artifacts/candidate-07 \
  --data-root .research-data/candidate-07 \
  --max-weeks 1
```

세 주간 통과 후에만:

```bash
python research/candidate-07/candidate.py pipeline \
  --config research/candidate-07/config.json \
  --week-plan research/candidate-07/week_plan.json \
  --output artifacts/candidate-07 \
  --data-root .research-data/candidate-07 \
  --max-weeks 3 \
  --run-long
```

각 실행은 `run.json`, `data_manifest.json`, `metrics.json`, `events.jsonl`, `fills.csv`, `positions.csv`, `account.csv`, `nav.csv`, `trades.csv`, `scenario_diagnostics.json`을 남긴다.

## 알려진 실패 조건

- 1분 OHLCV proxy가 실제 흡수와 단순 변동성 꼬리를 구분하지 못할 수 있다.
- bar 안의 실제 경로와 호가 깊이를 알 수 없으므로 급격한 stop gap의 시장충격은 one-tick 모델보다 클 수 있다.
- rolling external range가 강한 추세에서 계속 재정의되면 같은 parent order의 연속 구간을 여러 episode로 오인할 수 있다. cooldown과 단일 slot으로 일부 완화하지만 독립성 진단이 필요하다.
- continuation 측정 확장은 알려진 반대 유동성이 없는 price discovery 구간의 proxy이므로, 지속성이 약한 range regime에서 실패할 수 있다.
- BTC 주간 성공은 ETH/SOL/XRP 이전 성공을 의미하지 않는다. BTC 세 주간·장기평가 이후 동일 논리로만 이전한다.

## 현재 상태

초기 상태기계, NautilusTrader 실행 경로, checksum 데이터 로더, 펀딩 replay, 위험 수량 산정, 단위 테스트와 자동 주간 검증 workflow가 구현되었다. 권위 있는 성과 상태는 브랜치 workflow artifact의 `pipeline_summary.json`과 `week-1/metrics.json`으로 갱신한다.
