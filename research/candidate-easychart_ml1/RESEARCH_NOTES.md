# ML1 research notes

## 연구 중심

가격은 고정 도형의 결과가 아니라 여러 시간척도의 재고 조정, 공격 주문, 수동 유동성,
손절 주문이 만나는 연속 경매의 결과입니다. OB, FVG, 추세선, 채널, fakeout/trap은
각각 독립된 마법 패턴이 아니라 다음 질문을 관찰하는 서로 다른 창입니다.

- 거래가 몰릴 사전 구조와 유동성은 어디인가?
- 경계를 가격이 거절했는가, 받아들였는가?
- displacement가 실제 후속 진행으로 연결되는가, 흡수되는가?
- 현재 움직임은 국지적 inventory transfer인가, 시장 공통 충격인가?
- 구조적 무효화점보다 반대편 목표가 먼저 도달할 가능성이 높은가?

ML은 이 질문들의 비선형 결합을 학습합니다. entry·stop·target을 대신 만들거나 고정
3% 위험을 바꾸지 않습니다.

## 승률과 꾸준한 복리의 의미

숙련된 인간 데이트레이더의 거래가 대략 70% 이상의 승률, 충분한 빈도, 평균 1~2R의
계획 RR을 보일 수 있다는 설명은 **목표 숫자를 맞추라는 뜻이 아닙니다.** 좋은 상태를
선택하고, 좋은 가격에서 진입하고, 가까운 구조적 무효화점과 현실적인 목표를 쓰면
그러한 결과가 연결되어 나타날 수 있다는 관찰입니다.

따라서 ML1은 다음을 하지 않습니다.

- 70% 승률을 loss function이나 probability threshold로 넣기
- calibration 구간에서 목표 승률에 가장 가까운 threshold 찾기
- 거래 수 quota 또는 coverage를 맞추도록 threshold 최적화하기
- 고R이 낮은 target-first 가능성을 보상하도록 예상 R만 최대화하기

모델은 개별 plan의 target-first 확률을 추정합니다. 후보는 이미 RR 1R 이상이어야 하며,
큰 RR은 실패 가능성이 더 높은 plan을 정당화하지 않습니다. target이 stop보다 먼저
도달할 가능성이 더 높고 비용 후에도 양수인 plan 중에서, target-first 가능성이 높은
것을 우선합니다. 실제 전체 승률은 결과로 관찰합니다.

실제 승률이 나쁘면 threshold를 60%, 70%로 올려 숨기지 않습니다. setup context,
liquidity, direction, entry, confirmation, invalidation, target과 causal-event ownership 중
무엇이 잘못되었는지 거래별로 고칩니다.

## 보존하는 EasyChart 논리

- 목표 구간이 오지 않으면 진입하지 않는다.
- 손절은 구조 무효화, 목표는 반대편 유동성과 사전 구조에서 정한다.
- 상위 시간척도는 context, 하위 시간척도는 entry response에 더 적합하다.
- OB/FVG 자체보다 liquidity absorption, displacement와 confluence가 중요하다.
- 추세선·채널은 방향·속도·경계이며 break/re-entry/retest 순서가 중요하다.
- fakeout/trap은 구조 밖 liquidity sweep과 구조 안 복귀를 하나의 causal episode로 본다.

`스마트 머니 의도`를 보이지 않는 agent label로 사용하지 않고 sweep, displacement,
response, flow, objective geometry처럼 관찰 가능한 현상으로 번역합니다.

## 모델과 데이터

첫 모델은 작은·중간 표본에서 빠르게 반복하고 비선형 confluence를 다룰 수 있는 얕은
ExtraTrees입니다. 별도의 시간순 calibration 구간에서 확률을 보정하며 종목 이름은
입력 feature로 사용하지 않습니다. sequence model은 현재 모델이 반복적으로 놓치는
시장 과정이 거래 사례에서 확인될 때만 고려합니다.

Shadow run에서 중요한 것은 종합 점수표가 아니라 다음 거래 단위 질문입니다.

- 기존 gate가 거절했지만 실제 target-first였던 후보는 어떤 맥락이었는가?
- 허용된 손실은 구조 오류인가, 늦은 진입인가, 잘못된 방향인가, 비현실적 목표인가?
- OB/FVG/수평 flip/diagonal/pullback은 어떤 상태에서 서로 다른 역할을 하는가?
- 거래량과 공격자 흐름이 진행, 흡수, exhaustion 중 무엇을 보여주었는가?
- 동시에 나온 후보 중 더 높은 target-first 가능성을 고른 것이 continuous NAV를
  실제로 개선했는가?

그 답을 가장 잘 드러내는 짧은 구간부터 실행하고, 시장 논리와 코드가 어긋난 부분을
고친 뒤 필요한 만큼만 확대합니다.
