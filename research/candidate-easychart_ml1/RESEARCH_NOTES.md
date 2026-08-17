# ML1 research notes

## ML의 역할

가격은 서로 다른 시간척도의 재고 조정, 공격 주문, 수동 유동성, 손절 주문이 만나는
연속 경매의 결과다. OB, FVG, 추세선, 채널, fakeout/trap은 독립된 마법 패턴이 아니라
다음 질문을 관찰하는 서로 다른 창이다.

1. 거래가 몰릴 사전 구조와 유동성은 어디인가?
2. 경계를 거절했는가, 받아들였는가?
3. 국지적 inventory transfer인가, 네 종목 공통 정보 충격인가?
4. 구조 무효화점에 비해 현실적인 반대편 목표까지 충분한 공간이 있는가?
5. 공격자 흐름이 가격 진행으로 변환되는가, 흡수되는가?

RE1의 boolean gate는 이 질문을 각각 잘라 판단했다. ML1은 구조를 새로 발명하지 않고
이 변수들의 비선형 결합으로 `P(target before stop)`을 추정한다.

## 고정된 실행 계약과 ML의 경계

구조 엔진이 entry, stop, target과 gross RR을 결정한다. gross RR은 최소 1.0R이다.
주문이 선택되면 기존 실행층이 stop에서 현재 NAV의 약 3%가 손실되도록 수량을 정한다.

ML은 다음을 하지 않는다.

- risk fraction 변경
- confidence에 따른 수량 축소 또는 확대
- 별도 exposure cap
- 하루 손실 제한
- 연속 손실 cooldown
- 거래 횟수 quota
- 계좌 규모를 이유로 한 임의의 유동성 haircut
- entry, stop, target 가격 생성

따라서 3% fixed risk 위에 또 다른 “안전” 계층을 쌓지 않는다. 모델의 일은 나쁜 거래를
피해 안정적으로 보이는 시스템을 만드는 것이 아니라, 실제 target-first 확률과 RR을
함께 사용해 더 높은 비용 후 알파를 선택하는 것이다.

## 의사결정

각 frozen plan에 대해 다음을 계산한다.

```text
EV_net_R = p * win_net_R + (1 - p) * loss_net_R
```

`p`는 calibration된 target-first 확률이다. Runtime 경계는 후보 자신의 비용 후
break-even, 즉 `EV_net_R > 0`뿐이다. 별도의 minimum probability, probability edge,
목표 승률 75%, minimum coverage, uncertainty penalty를 추가하지 않는다.

이 경계는 추가 위험관리 규칙이 아니라 모델이 양의 알파라고 주장하기 위한 최소한의
논리다. 동시에 여러 양의 기대 후보가 있으면 expected R로 순위를 정한다. 모델의 tree
분산은 진단 기록일 뿐 거래를 보수적으로 낮추는 tie-breaker로 쓰지 않는다.

## 보존한 시장 논리

- naked chart의 institutional footprint와 market structure를 함께 본다.
- OB/FVG 존재 자체보다 유동성 흡수, 의미 있는 구조, displacement와 반응이 중요하다.
- 목표한 위치가 오지 않으면 진입하지 않는다.
- 추세선·채널은 방향, 속도, 경계이며 break/re-entry/retest를 하나의 사건으로 본다.
- fakeout/trap은 사전 구조 밖 sweep과 구조 안 복귀를 연결한 causal episode다.
- 손절은 구조 무효화, 목표는 반대편 유동성 또는 사전 구조에서 정한다.
- 상위 시간척도는 context, 하위 시간척도는 entry response에 더 적합하다.

“스마트 머니 의도”를 관측 불가능한 정답 label로 사용하지 않는다. sweep, displacement,
response, aggressor flow와 objective geometry로 번역한다.

## 재사용한 방법

- NautilusTrader의 주문, fill, fee, portfolio accounting과 reduce-only 보호 주문
- prior-only aggressor-flow baseline과 price progress/absorption
- 시간순으로 분리된 probability calibration
- label horizon이 split을 넘는 row의 purge
- raw symbol identity를 제외한 pooled four-market model

이 방법들은 미래정보와 계산 오류를 막고 확률을 해석 가능하게 하기 위한 연구 유효성
조건이다. 별도의 안정성 점수표나 통과 시스템을 만들기 위한 것이 아니다.

## 다음 실험의 핵심

- 기존 gate가 거절했지만 target-first였던 후보의 공통 맥락은 무엇인가?
- 기존 gate가 허용했지만 stop-first였던 후보는 무엇을 놓쳤는가?
- scenario family별로 flow, macro context와 geometry의 상호작용이 어떻게 다른가?
- ML이 좋은 거래를 늘리는가, 아니면 단순히 거래를 줄여 숫자를 좋게 보이게 하는가?
- expected-R arbitration이 earliest-causal 선택보다 continuous NAV를 개선하는가?
- 손실 원인이 구조 이해, entry timing, stop geometry, objective 선택 중 어디에 있는가?

진전은 보수적 필터 수가 늘어나는 것이 아니라, 비용 후 알파가 더 잘 구분되고 실제
fixed-risk continuous NAV가 더 강해지는 것이다.
