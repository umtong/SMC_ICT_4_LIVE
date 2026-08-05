# candidate-03 — LCPT-v1

**Liquidation Cascade Propagation with Structural Trailing**

이 디렉터리는 다른 후보와 조립되지 않는 독립 후보다. 가격 패턴 자체가 아니라
**현물·무기한선물의 공격 흐름과 미결제약정 감소가 함께 만드는 포지션 청산 연쇄**를
상태로 추적한다.

## 현재 판정

- 중심 논리와 실행 규칙은 고정됐다.
- 정확한 Binance aggregate-trade 순서로 세 개발 주를 재현했다.
- 새 검증 주간은 코드·설정·선정 salt를 고정한 뒤 다음 순서로만 연다.
  1. `2023-04-10`
  2. `2025-02-03`
  3. `2025-10-06`
- 앞 주가 게이트를 통과한 경우에만 다음 주가 실행된다.
- 세 주를 모두 통과하기 전에는 장기평가를 실행하지 않는다.

## 시장 구조 가설

가격 충격이 신규 포지션 축적만으로 만들어진다면 미결제약정이 증가하거나 유지될 수
있다. 반대로 다음 순서가 나타나면, 기존 반대 포지션이 시장가 주문으로 축소되면서
추가 청산과 손절을 부르는 전파 상태일 가능성이 높다.

```text
5분 가격 충격
+ 충격 방향 무기한선물 공격 흐름
+ 현물이 강하게 반대하지 않음
+ 미결제약정 감소
        ↓
다음 5분에도 같은 방향 가격 진행
+ 현물·선물 공격 흐름 정렬
+ 더 큰 미결제약정 감소
        ↓
1분 동안 원래 cascade 극값이 무효화되지 않음
        ↓
첫 실제 aggregate trade에서 진입
        ↓
목표 / 초기 무효화 / 비용 후 이익보호 구조 / 시간 종료
```

이 후보의 `displacement`는 큰 캔들이 아니다. **가격·현물 흐름·선물 흐름의 방향이
같고 OI가 두 구간 연속 감소하는 이동**이다. 두 번째 구간의 방향 지속은 단순 종가
돌파가 아니라 청산 흐름의 `BOS` 확인으로 사용한다.

## 검출기와 시나리오의 분리

### 검출기

`lcpt_features.py`는 다음 관측 사실만 계산한다.

- 5분 선물 가격 수익률
- 현물·선물 공격 명목금액 불균형
- 5분 OI 변화
- 두 cascade 구간의 고가·저가
- 점화 완료 시점까지의 60분 방향성 확장
- 완료된 60분 true-range 평균

### 시나리오

`lcpt_engine.py`가 다음 상태 전이를 담당한다.

```text
IDLE
  -> IGNITION_CANDIDATE
  -> CASCADE_CONFIRMED
  -> ENTRY_BUFFER
      -> INVALIDATED
      -> POSITION_ACTIVE
  -> CLOSED
```

한 종목에서조차 미체결 신규 진입과 포지션의 합은 하나다. 다종목 확장에서도 같은
단일 슬롯 계약을 유지한다.

## 고정 중심 사양

| 구성요소 | 고정값 |
|---|---:|
| 첫 5분 가격 충격 | `abs(return) >= 10 bp` |
| 첫 5분 OI 감소 | `>= 1 bp` |
| 첫 5분 선물 흐름 | 가격 방향과 정렬 |
| 첫 5분 현물 흐름 | 가격 방향 기준 `>= -0.10` |
| 다음 5분 가격 | 같은 방향 진행 |
| 다음 5분 현물·선물 흐름 | 모두 같은 방향 |
| 다음 5분 OI 감소 | `>= 20 bp` |
| 사건 수명 | 점화 종가까지의 60분 방향 이동 `<= 50 bp` |
| 진입 관찰 | 1분 |
| 진입 | 관찰 종료 후 첫 futures aggregate trade |
| 초기 손절 | 두 cascade 구간 극값 `± 0.20 × ATR(60m)` |
| 비용 후 목표 | 최대 펀딩까지 포함해 최소 `+6R` |
| 이익보호 활성화 | MFE `2R` |
| 최소 잠금 | 현재 비용 후 `+0.5R` |
| 구조 추적 | 최근 완료 20분 극값 `± 0.05 × ATR` |
| 최대 보유 | 240분 |
| 거래 위험 | 현재 전체 NAV의 3% |
| 수수료 | 각 체결 5 bp |
| 슬리피지·시장충격 | 각 체결 1.5 bp |
| 펀딩 | 8시간당 1 bp |

### 60분 확장 정의

확장 필터는 확인 구간까지 포함하지 않는다.

```text
confirmation T
ignition      [T-10m, T-5m)
continuation  [T-5m, T)

extension =
  close(T-5m) / close(T-65m) - 1
```

즉 **점화 구간은 포함하고 확인 구간은 제외한 60분 이동**이다. 점화가 끝난 시점에
이미 알 수 있는 사건 수명이다. 두 cascade 구간을 모두 제외하는 통제에서는 오래된
흐름이 새 사건으로 추가되어 승률과 mark-to-market 낙폭이 악화됐다.

## 수량과 비용

```text
계획 손실 = 현재 전체 NAV × 0.03

1개당 예상 손실 =
  |예상 진입 체결가 - 예상 손절 체결가|
  + 진입 수수료
  + 손절 수수료
  + 진입·손절 슬리피지와 시장충격
  + 최대 보유시간 예상 펀딩

수량 = 계획 손실 / 1개당 예상 손실
```

모델 점수, 방향, 변동성에 따라 위험률을 바꾸지 않는다. 목표가격은 최대 예상 펀딩을
지급해도 비용 후 `+6R` 이상이 되도록 역산한다. 이익보호 가격은 그 시점까지 발생한
펀딩을 포함해 비용 후 `+0.5R`을 잠근다.

## 개발 주간 정확 체결 결과

각 결과는 futures aggregate trade를 두 번 읽는다. 첫 번째 패스는 완료 분봉과 상태
특징을 만들고, 두 번째 패스는 정확한 체결 순서로 진입·손절·목표를 재생한다.
MDD는 거래 종결 NAV가 아니라 **매 aggregate trade의 예상 청산가치 NAV**로 계산한다.

| BTC 주간 | 거래 | 승률 | 평균 비용 후 R | 비용 후 NAV | 일평균 기하성장 | MTM MDD |
|---|---:|---:|---:|---:|---:|---:|
| 2022-03-07 | 11 | 54.55% | +1.150R | +40.88% | +5.02% | 10.62% |
| 2025-03-17 | 8 | 62.50% | +0.781R | +19.36% | +2.56% | 6.50% |
| 2022-07-18 | 14 | 50.00% | +0.467R | +18.93% | +2.51% | 19.07% |

`results/development_summary.json`과 주별 `trades.csv`가 중심 설정의 재현 결과를
보존한다. 이 세 주는 모두 개발에 사용됐으므로 미공개 성공 근거가 아니다.

## 변인 통제

중심점만 선택하지 않았다. 보수적인 1분 adverse-first screening에서 다음 범위가 세
개발 주 모두 거래수·승률·비용 후 일평균 1%·MDD 게이트를 통과했다.

- 점화 가격 충격: 8–11 bp
- 첫 OI 감소: 0–1.5 bp
- 다음 OI 감소: 19–20 bp
- 첫 현물 흐름 하한: −0.20–0.00
- 점화까지 60분 확장: 50–55 bp
- 손절 buffer: 0.10–0.30 ATR
- 비용 후 목표: 4–8R
- 보호 활성화: 1.5–3R
- 잠금: 0–1R
- 구조 추적: 10–40분
- 최대 보유: 180–360분

고정 중심점은 이후 exact aggregate-trade 순서에서 다시 통과했다.

## 데이터 무결성

- Binance Vision futures USD-M `aggTrades`
- Binance Vision spot `aggTrades`
- Binance Vision futures USD-M `metrics`
- 각 ZIP은 공급자 `.CHECKSUM`으로 확인한다.
- 파일별 SHA-256, 크기, 행 수를 `run.json`과 `metrics.json`에 남긴다.
- Binance metrics의 명시적 `0E-8` OI는 경제적 OI 0으로 해석하지 않는다. 공급자
  결측으로 제외하고 OI 변화 연속성을 재시작한다.
- 분봉·5분봉은 이벤트가 모두 끝난 뒤에만 관측 가능하다.
- 진입은 1분 관찰 종료 후 첫 실제 aggregate trade다.

## 실행

환경 설치를 반복하지 않는다.

```bash
smc4 doctor

PYTHONPATH=src:research/candidate-03 \
  python research/candidate-03/test_lcpt.py

python research/candidate-03/download_lcpt_bundle.py \
  --symbol BTCUSDT \
  --week-start 2023-04-10 \
  --output .research-data/candidate-03/lcpt/validation-1

PYTHONPATH=src:research/candidate-03 \
  python research/candidate-03/run_lcpt.py \
  --futures-agg .research-data/candidate-03/lcpt/validation-1/futures-agg/*.zip \
  --spot-agg .research-data/candidate-03/lcpt/validation-1/spot-agg/*.zip \
  --metrics .research-data/candidate-03/lcpt/validation-1/metrics/*.zip \
  --week-start 2023-04-10 \
  --label btc-lcpt-validation-1 \
  --output artifacts/candidate-03/lcpt/validation-1

python research/candidate-03/gate.py \
  artifacts/candidate-03/lcpt/validation-1/metrics.json \
  --minimum-trades 8 \
  --minimum-win-rate 0.45 \
  --minimum-daily-growth 0.01 \
  --require-target
```

각 실행은 다음을 남긴다.

```text
run.json
metrics.json
signals.csv
trades.csv
scenario_events.jsonl
```

## 알려진 실패 조건

- OI 감소와 가격 전파가 포지션 청산이 아니라 거래소 간 재배치·헤지 이동일 수 있다.
- 공개 OI는 5분 스냅샷이므로 구간 내부의 증가 후 감소 순서를 알 수 없다.
- 현물·선물 aggregate trade는 실행 흐름을 보여주지만 L2 큐 재충전과 이 계좌의
  실제 시장충격을 직접 보여주지 않는다.
- 뉴스·대규모 청산 gap에서는 손절 체결이 예상보다 나빠 계획 손실 3%를 넘을 수 있다.
- 2022-07-18 개발 주의 MTM MDD가 20% 게이트에 가깝다. 미공개 주에서 낙폭이
  확대되면 후보를 통과시키지 않는다.
- 조용한 시장에서는 OI 연쇄 사건이 부족해 주당 8거래를 만들지 못할 수 있다.
- BTC 검증을 통과하더라도 ETH·SOL·XRP에서 동일 상태 방향이 유지되는지는 별도
  검증이 필요하다.
- 한 종목에 맞춘 임계값 변경은 허용하지 않는다. 종목 확장 시 가격·수량 정밀도와
  비용·데이터 단위만 instrument metadata에 따라 달라진다.

## 폐기 이력

- 1분봉 liquidity-sweep 분류기는 첫 주 비용 후 기대값이 음수여서 폐기했다.
- FAR-v1은 흡수 관측을 즉시 반전으로 오인해 두 번째 주에서 붕괴했다.
- FAR-v2는 반대 방향 CHoCH를 추가했지만 첫 미공개 주 `2022-07-18`에서 5전
  전패해 폐기했다.
- 고정 세션 돌파와 일반 압축 돌파도 두 번째 주에서 효과 방향이 유지되지 않아
  폐기했다.

LCPT는 이 실패에서 **반전을 예측하지 않고, OI 감소로 확인된 강제 흐름의 지속만
거래한다**는 방향으로 전환한 후보다.
