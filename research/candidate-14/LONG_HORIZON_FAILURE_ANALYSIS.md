# 무작위 주간 통과 후 장기평가 붕괴의 구조적 원인

## 결론

문제는 “무작위 주간이 우연히 쉬웠다” 하나가 아니다. 이 저장소의 반복된 실패는 다음 네 층이 결합해 만들어졌다.

1. **선택 절차가 주간을 사실상 학습 데이터로 바꾼다.**
2. **같은 표면 패턴이 서로 다른 잠재 경매 상태를 섞는다.**
3. **거래 수가 독립 사건 수보다 훨씬 크게 보인다.**
4. **독립 주간 NAV의 곱이 연속 계좌의 장기 경로를 대체한다.**

따라서 짧은 주간은 구현 오류와 명백한 논리 오류를 빠르게 분리하는 도구일 뿐, 알파의 장기 존재를 증명하는 표본이 아니다. 한 주가 무작위로 선택되었더라도 그 주의 결과를 보고 시나리오, 목표, 보유시간, peer 수, 확인 강도 또는 실행 분기를 선택하면 그 주는 홀드아웃이 아니다.

---

## 저장소에서 확인된 반복 패턴

### Candidate 02: 무작위 주간 안의 파라미터 선택

`v77_first_week_decision.json`은 첫 주간에서 목표배수와 최대보유시간 조합을 동시에 평가하고, 통과 plateau의 중앙점 `1.25R / 180분`을 선택했다.

- 첫 주: 9거래, 7승 2패, 일 기하성장률 `+1.1823%`, PF `3.093`
- 잠근 두 번째 주: 5거래, 2승 3패, 일 기하성장률 `-1.0220%`, PF `0.232`

날짜는 무작위였지만 규칙이 그 날짜에서 선택되었으므로 첫 주 성과는 선택 편향을 포함한다. 인접 조합이 함께 통과했다는 plateau도 표본이 한 주이면 시장 상태 하나를 공유하므로 독립적인 강건성 증거가 아니다.

### Candidate 03: 두 주 통과 후 세 번째 주의 잠재 상태 혼합

V11은 앞선 두 주에서 각각 일 `+2.8035%`, `+1.1631%`를 기록했으나 세 번째 동결 주에서 일 `-0.5990%`로 실패했다.

실패 분석은 동일한 `VALUE_EDGE_CONTINUATION` 라벨이 다음을 함께 담았음을 확인했다.

- 청산 이후의 진짜 방향성 repricing
- 미해결 또는 반대 inventory cycle 내부의 일시적 displacement

또한 migration reclaim 승리는 `+0.179R`, `+0.411R`에 그쳤고 한 번의 정상 손실은 `-1R`이었다. 방향 판정 일부가 맞아도 지급 구조가 음수였다. 필터를 더하면 7개였던 episode가 더 줄어 목표 성장에 필요한 기회 밀도 자체가 사라졌다.

### Candidate 12: 같은 sweep/reclaim의 rejection–acceptance 혼동

I4는 첫 주 5전 5승, 일 `+2.6930%`였으나 다음 untouched 주에서 2전 2패, 일 `-0.7918%`였다.

두 손실의 reclaim body는 각각 약 `0.72 ATR`, `0.28 ATR`로 약했고, 이후 가격은 raid extreme 위로 displacement했다. 첫 주의 high sweep/reclaim은 실패경매였지만 두 번째 주의 표면상 유사 사건은 boundary acceptance였다. “sweep 후 안쪽 종가”만으로 reversal 상태를 고정한 것이 실패 원인이었다.

### Candidate 10: 거래 수와 독립 사건 수의 차이

V4는 한 주에 34거래가 있었지만 `2023-10-16 05:16–05:17` 한 cascade에서 13거래가 파생되었다.

- 13거래 중 11패
- 이 cluster 순손실 `-48,557 USDT`
- 전체 순손실의 약 `96.9%`

수치상 34개의 표본이 아니라, 동일 유동성 원인에 반복적으로 반응한 하나의 큰 실패와 소수의 다른 사건이었다. 경계값이 매 print마다 바뀌면서 하나의 liquidity cause가 여러 scenario ID로 분열되었다.

### Candidate 01: 선택성–지연의 역설과 순차 가설 생성

여러 버전에서 구조 확인을 추가하면 거짓 신호는 줄었지만, 확인 완료 시점에는 목표까지의 비용 후 거리가 소진되었다.

- V21: MSS/retest는 기존 손실 진입을 제거했지만 새 계획은 모두 비용 후 geometry 부족
- V28: completed resumption을 기다리자 목표 거리가 소진
- V29: conditional timing을 도입해도 원래 impulse stop과 이미 소비된 목표 때문에 구조적 R 부족
- V39: two-peer 합의는 한 주에 단 한 번 거래했고 그 한 번이 승리해 100% 승률처럼 보임
- V41: 6거래의 부분집합에서 “정확히 2 peer”가 3승 1패, “3 peer”가 0승 2패였고 이것이 다음 버전 가설이 됨

각 변경은 시장 논리로 설명할 수 있어도, 손실 표본의 사후 부분집합이 다음 규칙이 되는 순간 연구 자유도가 누적된다. 버전 수가 많아질수록 어느 한 주에서 매우 좋은 후보가 나타날 확률은 높아진다.

### Candidate 13/14: 완벽한 승률처럼 보이는 희소 표본

- Candidate 13: 35일, 7거래, 7승
- Candidate 14 V5: 35일, 8거래, 8승

8전 8승의 95% Wilson 승률 하한은 약 `67.6%`다. 100% 관측 승률의 하한이 80%를 넘으려면 최소 16전 16승이 필요하다. 7~8개의 승리로 희귀한 손실 상태, 손실 군집, 레짐별 조건부 실패율을 추정할 수 없다.

---

## 왜 “무작위”가 충분하지 않은가

### 1. 날짜 무작위화와 연구 독립성은 다르다

다음 중 하나라도 발생하면 그 주는 학습 데이터다.

- 여러 시나리오 중 통과한 것을 선택
- 목표, stop, hold, peer 수, session route를 비교
- 손실 거래의 공통점을 다음 필터로 승격
- 통과할 때까지 새 버전을 생성
- 통과 후보만 다음 주로 전진

무작위 날짜는 날짜 선택 편향만 줄인다. **규칙 선택 편향과 중단 시점 편향은 그대로 남는다.**

### 2. “첫 주 통과” 조건이 winner's curse를 만든다

실제 기대값이 같아도 표본 변동이 큰 후보가 첫 주 상단에 더 자주 나타난다. 통과한 후보의 관측 기대값은 진짜 기대값보다 위로 편향된다. 다음 주나 장기평가에서 평균으로 회귀하면서 붕괴한다.

### 3. 한 주는 한두 개 잠재 상태만 포함한다

SMC/ICT 표면 사건은 동일해도 시장의 숨은 상태는 다르다.

```text
sweep + reclaim
├─ 재고가 소진된 실패경매
├─ 정보 주문의 일시적 pullback
├─ liquidation 뒤 계속되는 price discovery
├─ broad balance 내부의 noise
└─ 다른 시장이 주도하는 cross-asset transfer
```

한 주에서 첫 번째만 나타나면 reversal 규칙이 완벽해 보인다. 장기평가에서는 나머지가 섞인다.

### 4. 거래는 독립 Bernoulli 시행이 아니다

- 같은 cascade의 여러 scenario ID
- 같은 날 반복되는 동일 방향 inventory shock
- BTC/ETH/SOL/XRP의 동시 market-wide event
- 동일 session range에서 연속 발생한 acceptance/rejection
- 한 레짐이 며칠 지속되며 만드는 loss streak

따라서 거래 20회가 독립 표본 20개라는 가정은 대개 틀리다. 유효 표본 수는 causal episode 수와 regime block 수에 더 가깝다.

### 5. 승률·R·빈도는 잠재 상태에 조건부다

짧은 주간에서는 다음 곱 중 한 항만 좋게 관측될 수 있다.

```text
달력일 성장
≈ 시나리오 발생률
× 체결 확률
× 조건부 기대 로그수익
× 독립성 보정
```

활성 주간만 보면 발생률이 조건부로 1이 된다. 장기 달력에는 무거래 주간과 낮은 체결률이 포함되어 성장률이 낮아진다.

### 6. 확인을 늘릴수록 경제적 공간이 사라진다

SMC/MSS/FVG 확인은 방향 오류를 줄일 수 있지만, 완료된 봉을 계속 기다리면:

- entry가 target에 가까워짐
- causal stop은 그대로 멂
- 수수료와 spread가 구조적 손실에서 차지하는 비중이 커짐
- 좋은 움직임은 미체결 또는 이미 소비됨

따라서 “더 확실한 확인”은 자동으로 더 좋은 전략이 아니다. 확인 사건은 진입 뒤 필터가 아니라 **새로운 auction leg의 시작**이어야 한다.

### 7. 목표와 무효화가 서로 다른 경매 leg에 속한다

반복된 실패에서 다음 조합이 나타났다.

- stop은 최초 initiative 전체를 보호
- entry는 늦은 resumption 확인 후
- target은 최초 projection의 이미 소비된 부분

이 경우 방향이 맞아도 비용 후 R은 음수다. entry, invalidation, objective는 동일한 시나리오 leg에서 정의되어야 한다.

### 8. 비용은 고정 상수가 아니라 상태 변수다

stress cascade에서는 수 밀리초 동안 가격이 크게 이동한다. 정상 주간의 2 tick 또는 고정 bp reserve는 장기 tail event를 표현하지 못할 수 있다. 비용 모델을 더 정교하게 만드는 것이 알파 연구의 목적은 아니지만, **시나리오가 stress impact를 발생시키는 상태인지**는 논리의 일부다.

### 9. 독립 주간 NAV 곱은 연속 장기평가가 아니다

기존 Candidate 14 집계는 각 주를 100,000 NAV로 다시 시작하고 주별 NAV ratio를 곱했다. 이 방식은 다음을 제거한다.

- 주간 경계를 넘는 position/order
- 오래된 liquidity pool의 생성·소비·만료
- 이전 손실 뒤 줄어든 실제 수량
- 연속 레짐에서의 loss clustering
- 실제 peak에서 이어지는 drawdown
- 무거래 기간의 state persistence

주별 최대 drawdown의 최댓값은 연속 계좌 최대 drawdown과 같지 않다. 주간 자료는 진단 표이지 최종 계좌 증거가 아니다.

### 10. 주간 종료 강제청산과 경계 검열

각 주 끝에서 강제 flat하면 다음이 달라질 수 있다.

- 장기 보유 손실이 잘림
- 다음 주에 이어질 target/stop이 관측되지 않음
- 마지막 날 신호가 별도 초기화 주에서는 사라짐
- warmup마다 pool history가 새로 구성됨

한 번의 엔진으로 연속 실행해야 경계 효과가 제거된다.

---

## 앞으로의 연구 계약

### A. 짧은 주간의 역할 제한

짧은 주간은 다음에만 사용한다.

1. 구현 오류와 논리 오류 분리
2. 시나리오가 실제로 발생하는지 확인
3. 주문·위험·체결 계약 검증
4. 명백히 음수인 후보 조기 폐기

짧은 주간에서 목표 수익을 통과했다는 이유만으로 알파를 확정하지 않는다.

### B. 시나리오 상태를 결과가 아니라 전이로 정의

각 scenario는 반드시 다음을 기록한다.

```text
source liquidity identity
→ first initiative
→ acceptance / rejection evidence
→ internal structure transition
→ entry leg
→ invalidation leg
→ still-live objective
→ terminal reason
```

동일 source event에서 파생된 반복 신호는 하나의 economic episode로 묶는다.

### C. 연속 계좌 검증

최종 후보는 다음을 만족해야 한다.

- NautilusTrader 엔진 한 번만 시작
- 시작 NAV 한 번만 설정
- 중간 주간 reset 없음
- 전역 pending entry + position 최대 1
- 전체 기간 종료 때만 강제 flat
- 달력 주간 표는 동일 계좌 경로의 slice로만 계산

### D. 통계적 불확실성 명시

관측 승률과 함께 Wilson 95% 구간을 기록한다. 8전 8승을 “100%에 가까운 전략”으로 해석하지 않는다. 거래 수뿐 아니라 다음을 함께 기록한다.

- active calendar weeks
- 최대 연속 무거래 주
- 최대 한 날 거래 집중도
- scenario/symbol/week 성장 집중도
- causal episode 수
- 동일 regime block 내 loss streak

### E. 결과를 본 뒤 규칙을 바꾸면 새 후보

홀드아웃 손실을 보고 조건을 바꾸면 그 기간은 영구적으로 개발 자료가 된다. 동일 기간에서 고친 후보를 다시 “검증”하지 않는다. 새 버전은 새로 예약한 연속 구간에서만 최종 판단한다.

---

## Candidate 14에 적용한 변경

이번 연구에서 전략 파일과 임계값은 바꾸지 않았다. V5 소스를 그대로 동결하고 다음 연속 검증을 사전 예약했다.

- interval: `2026-05-11`–`2026-08-03`
- length: 84 calendar days / 12 complete weeks
- engine: one NautilusTrader account
- weekly reset: prohibited
- starting NAV: 100,000 once
- planned loss: current NAV × 3%
- minimum closed trades: 18
- minimum active calendar weeks: 9/12
- minimum observed win rate: 80%
- minimum Wilson 95% win-rate lower bound: 50%
- minimum payoff ratio: 1.2
- minimum daily geometric growth: 1%
- maximum continuous realized drawdown: 20%
- maximum one-week share of positive log growth: 35%
- maximum consecutive empty weeks: 2

이 검증은 “좋은 무작위 주간을 더 찾는 루프”가 아니라, 현재 후보가 한 번의 연속 계좌에서 실제로 살아남는지 확인하는 종료 시험이다.
