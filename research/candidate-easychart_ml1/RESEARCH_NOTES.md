# ML1 research notes

## 시장과 이 시스템에서 ML이 맡아야 할 일

가격은 확정적인 도형의 결과가 아니라, 서로 다른 시간척도의 재고 조정·공격 주문·
수동 유동성·손절 주문이 만나는 연속 경매의 결과다. OB, FVG, 추세선, 채널,
fakeout/trap은 서로 독립된 마법 패턴이라기보다 다음 공통 질문의 다른 관찰 창이다.

1. 거래가 몰릴 만한 사전 구조와 유동성은 어디인가?
2. 그 경계를 가격이 거절했는가, 받아들였는가?
3. 현재 움직임은 국지적 inventory transfer인가, 네 종목 공통 정보 충격인가?
4. 진입 뒤 무효화점까지의 거리에 비해 먼저 도달할 현실적 opposing liquidity는
   충분히 먼가?
5. 현재 공격자 흐름이 가격 진행으로 변환되는가, 아니면 흡수되는가?

기존 RE1은 이 질문을 각각 boolean gate로 번역하면서 좋은 후보도 맥락 하나 때문에
사라지고, threshold를 느슨하게 하면 나쁜 후보가 함께 늘어나는 한계가 있었다.
ML은 구조를 새로 발명하는 역할이 아니라, 위 질문들의 **비선형 결합**을 같은
conditional probability로 압축하는 데 사용한다.

## Source-derived logic retained

EasyChart 자료에서 보존한 핵심은 다음과 같다.

- naked chart에서 institutional footprint와 market structure를 함께 본다.
- OB/FVG 자체보다 유동성 흡수, 의미 있는 구조, 뚜렷한 displacement, 여러 근거의
  중첩이 중요하다.
- 목표한 구간이 오지 않으면 진입하지 않는다.
- 추세선·채널은 방향/속도/경계이며, wick 기준과 break/re-entry/retest가 중요하다.
- fakeout/trap은 사전 구조 밖의 liquidity sweep과 구조 안 복귀를 하나의 causal
  episode로 본다.
- 손절은 구조 무효화, 목표는 반대편 유동성/사전 구조에서 정한다.
- 상위 시간척도는 context, 하위 시간척도는 entry response에 더 적합하다.

자료의 “스마트 머니 의도” 서술을 사실 검증된 hidden agent label로 사용하지 않는다.
코드에서는 관찰 가능한 sweep, displacement, response, flow, objective geometry로
번역한다.

## External methods reused

- NautilusTrader: 기존 RE1의 동일 전략 코드 경로, 주문 상태, fill/fee/portfolio
  accounting, reduce-only 보호주문을 그대로 사용한다.
- Order-flow imbalance: 단순 거래량보다 signed aggressor flow와 price progress의
  결합을 사용한다. 1분 quote volume, trade count, taker-buy quote volume은 현재 봉을
  제외한 prior baseline에 비교한다.
- Probability calibration: forest 점수를 곧바로 확률이라 부르지 않고, 별도의 시간순
  calibration 구간에서 sigmoid/Platt mapping을 학습한다.
- Selective classification: 모든 후보에 답을 강요하지 않고 post-cost expectancy가
  부족하면 abstain한다.
- Purged time split: target/stop label이 다음 시간 구간까지 걸친 plan은 이전 split에서
  제거한다.

## What is intentionally not learned in ML1

- raw next-bar direction
- entry, stop, target 가격
- 주문 수량, leverage, risk fraction
- 하루 손실 제한이나 거래 횟수 quota
- 종목 이름을 이용한 전용 규칙
- 미래 MFE/MAE를 feature로 사용하는 행위
- backtest 결과를 보고 runtime에서 threshold를 즉석 변경하는 행위

## Label and decision

각 frozen plan의 binary event는 `target before stop`이다. 동일 1분봉에서 둘 다 닿으면
bar data로 순서를 알 수 없으므로 stop으로 처리한다. 모델 확률 `p`와 실행 가정으로

```text
EV_net_R = p * win_net_R + (1 - p) * loss_net_R
```

를 계산한다. 필요한 최소 확률은 각 plan의 비용 후 break-even probability와
calibration에서 선택한 probability edge 중 더 높은 값이다. 이 때문에 1.0R plan과
3.0R plan을 같은 고정 confidence threshold로 취급하지 않는다.

## Why a shallow ExtraTrees ensemble first

현재 병목은 시계열 전체를 생성하는 문제가 아니라 이미 의미가 있는 사건의 선별이다.
따라서 첫 구현은 다음 성질을 우선한다.

- 수치·범주형 confluence의 비선형 상호작용
- 스케일링에 덜 민감
- 작은/중간 표본에서 빠른 반복
- portable tree로 정확히 export 가능
- feature importance와 leaf behavior를 추적 가능

sequence model이나 end-to-end deep network는 후보 label과 regime coverage가 충분히
쌓이고, shallow selector가 놓치는 반복 가능한 failure mode가 확인된 뒤 고려한다.

## Most informative next experiment

첫 shadow run에서 중요한 것은 전체 점수 하나가 아니라 다음을 확인하는 것이다.

- 기존 V2 gate가 거절했지만 target-first였던 후보가 실제로 얼마나 존재하는가?
- OB/FVG/수평 flip/diagonal/pullback 중 어느 mechanism에서 flow와 macro context의
  상호작용이 달라지는가?
- 모델이 비용 후 승리 확률을 높이면서 하루당 독립 기회를 지나치게 줄이는가?
- simultaneous candidates에서 earliest-causal 선택보다 expected-R arbitration이
  실제 continuous NAV를 개선하는가?
- 손실이 구조 자체의 오류인지, 늦은 entry/너무 먼 stop/가까운 objective인지,
  common shock를 국지 reversal로 오인한 것인지?

이 질문에 답하는 최소 길이의 구간으로 먼저 실행하고, 결과에 따라 feature/후보
mechanism을 수정한다. 모델 종류를 늘리는 것 자체는 진전이 아니다.
