# EasyChart v2 understanding contract

이 문서는 성과 보고서가 아니라 **무엇을 이해했고 무엇을 아직 구현하지 않았는지**를 고정하는 연구 계약입니다. 구현하지 않은 개념의 효과를 백테스트 결과로 판단하지 않습니다.

## 1. 자료와 실제 거래에서 확인된 전체 의사결정 구조

EasyChart의 매매는 `OB`, `FVG`, 추세선, 채널, Fake out을 독립 패턴으로 각각 매수하는 방식도 아니고, 모든 도구를 매번 `AND`로 요구하는 방식도 아닙니다.

```text
상위 시간대 방향·주요 지지/저항
→ 현재 가격이 향하는 유동성/목표
→ 의미 있는 경계 또는 실행 구간
→ 그 구간과 가격의 상호작용
→ 거부·수용·지속·미해결 상태 판별
→ 해당 상태에 필요한 실행 위치 또는 확인
→ 동일 파동의 무효화와 사전 존재 목표
→ 목표한 위치가 오지 않으면 NO TRADE
```

### 시간대별 역할

- 월봉·주봉·일봉: 큰 추세와 주요 지지/저항
- 12시간·4시간·1시간: 중간 추세, 패턴, 거래 구간
- 15분·5분·1분: 실제 진입 타이밍
- 한 시간대의 신호를 다른 시간대에서 반복 확인하는 것이 아니라, 시간대마다 서로 다른 역할을 맡깁니다.

### 구조물별 역할

- **오더블럭:** 모든 반대색 캔들이 아니라 유동성 흡수 또는 의미 있는 구조에서 출발한 몸통 구간입니다. causal origin, 실행 구간, 반대 저항/목표 중 하나가 될 수 있습니다.
- **FVG:** 모든 3캔들 갭이 아니라 눈에 띄는 변위가 만든 불균형입니다. displacement 흔적, 실행 구간, S/R flip과의 중첩, 반대 목표 중 하나가 될 수 있습니다.
- **추세선:** 방향, 이동 속도, 대중이 보는 동적 유동성 경계를 제공합니다. bounce, break-and-retest, fakeout을 서로 다른 상태로 route합니다.
- **채널:** 최소 세 점으로 확인된 평행 경계와 반대편 목표를 제공합니다. 네 번째 상호작용, 이탈·재진입, 강한 돌파를 구분합니다.
- **Fake out / Trap:** 독립 zone이 아니라 `축적/학습된 경계 → 구조 밖 유동성 excursion → 내부 복귀/안착 실패 → 반대 방향 이동`이라는 상태 전이입니다.

## 2. 실제 거래 사례가 고정한 family 경계

`Project/cases`의 프레임과 자막을 연결한 결과는 `casebook_index.json`과 `CASE_DERIVED_POLICY.md`에 기록합니다.

- case 02·14: 추세선 break → 첫 retest → retest 구간의 bullish OB. 별도 local sweep은 필수가 아니었습니다.
- case 21: FVG + S/R flip이 실행 위치, 작은 causal leg 저점이 무효화, 상단 OB가 목표였습니다.
- case 28: 15분·1시간 bullish OB 중첩이 직접적인 재매수 이유였습니다. 별도 local sweep은 명시되지 않았습니다.
- case 30: 일봉 추세선 reclaim, 확장 하락 채널 하단, 일봉 OB가 하나의 pullback thesis를 구성했습니다.
- case 34: 넥라인·로그 추세선 fakeout과 채널 4번째 포인트가 방향을, FVG/SR flip·OB·유동성 band가 실행 위치를, fakeout 고점이 공통 무효화를 제공했습니다.
- case 35: 반복 접촉된 추세선/쐐기 하단 break가 상태 전환, 4시간 bearish OB가 되돌림 실행 위치였습니다.

따라서 어느 한 도구나 어느 한 세부 순서를 EasyChart 전체의 보편 필수조건으로 만들지 않습니다.

## 3. 현재 코드가 실제로 구현한 것

### 공통 실행

- Binance 1분 외부 봉과 NautilusTrader 내부 합성 봉
- 네 종목 단일 continuous account, 전역 한 포지션
- 진입 직전 NAV 3% 계획손실 수량
- 진입 전 entry·stop·target과 gross RR 1.0 이상
- 단일 전량 진입·전량 stop·전량 target
- NautilusTrader bracket, 체결, 수수료, 포지션, 계좌 처리
- plan·arbitration·order tag·fill·trade window 감사 경로

### 시장 객체와 family

- `easychart_zones.py`: closed-candle 기준 OB/FVG geometry와 lifecycle
- `causal_swings.py`: 오른쪽 확인창 이후에만 관측되는 wick swing
- `causal_trendlines.py`: 확인된 wick pivot pair로 생성되는 causal trendline, close break, 첫 later retest, failed break lifecycle
- `easychart_mtf_scenario.py`: `60m/15m zone overlap → 5m local liquidity sweep → first size-confirmed 5m OB`라는 한 family 가설
- 기존 generic prominence pivot rejection/acceptance baseline

현재 코드는 **여러 부품과 일부 family**를 구현했을 뿐, EasyChart 전체 통합 시스템이 아닙니다.

## 4. 아직 구현·확정하지 않은 것

| 구성 요소 | 상태 | 현재 결과로 효과 판단 가능 여부 |
|---|---|---|
| case 02·14의 trendline break-first-retest + OB family | line lifecycle 구현, 완전 family 미구현 | 불가 |
| case 28의 sweep 없는 MTF OB overlap continuation | 현재 family가 sweep을 강제 | 불가 |
| S/R flip + FVG continuation | 미구현 | 불가 |
| fakeout reversal의 contraction·reclaim·retest | 부분 객체만 존재 | 불가 |
| 완전 평행 채널, 3점 확인, 4번째 interaction | 미구현 | 불가 |
| 여러 family의 하나의 state router | 미구현 | 불가 |
| 여러 human entry band를 단일-entry로 번역하는 고정 정책 | `first executable` vs `first confirmed` 비교 전 | 불가 |
| peer/USDT.D context의 독립 추가가치 | 미구현 | 불가 |
| 자료의 분할진입·부분익절·반익반본 | 프로젝트 계약상 의도적으로 미사용 | 최종 시스템에 사용하지 않음 |

## 5. 현재 결과가 말할 수 있는 범위

기존 손실 결과는 해당 시점에 실제로 구현된 다음 정책만 반증할 수 있습니다.

> `generic prominence pivot 또는 특정 MTF zone-sweep-first-OB 번역을 실제 EasyChart 전체와 동일시하는 것`은 잘못이며, 그 구현이 현재 비용 구조에서 약했습니다.

다음 결론은 내릴 수 없습니다.

- OB가 효과 없다.
- FVG가 효과 없다.
- 추세선이나 채널이 효과 없다.
- Fake out/Trap 전체 전략이 효과 없다.
- local sweep은 언제나 필요하다.
- EasyChart 방식이 효과 없다.

## 6. 구현 오류와 이해 오류를 분리하는 감사 기준

각 완료 거래와 누락 거래는 다음 질문에 답할 수 있어야 합니다.

1. 상위 시간대 context와 목표 유동성은 신호 전에 무엇이었는가?
2. source 구조는 언제 형성됐고 언제 관측 가능해졌는가?
3. OB/FVG/추세선/채널이 각각 어떤 역할을 맡았는가?
4. interaction, state transition, confirmation이 자료의 순서와 같은가?
5. 자료 사례라면 거래했어야 하는데 코드가 놓쳤는가?
6. 자료 사례라면 거래하지 않았어야 하는데 코드가 만들었는가?
7. 계획 entry와 실제 fill은 언제 얼마에 체결됐는가?
8. stop과 target은 같은 causal auction leg에 속했는가?
9. 신호 후 첫 retest 또는 planned zone이 실제로 남아 있었는가?
10. 다른 종목·family와 충돌해 거절된 후보는 무엇인가?
11. 손실은 논리 실패인가, 체결비용 실패인가, 구현 오류인가?
12. 수익 거래도 같은 시간적·인과적 검사를 통과하는가?

## 7. 다음 구현의 기준

다음 핵심 작업은 threshold를 더 붙이는 것이 아닙니다.

```text
causal wick trendline
→ close break
→ first retest
→ retest를 구성하거나 겹치는 OB
→ retest/formation invalidation
→ 가장 가까운 사전 존재 반대 유동성
```

이를 case 02·14의 프레임·자막과 먼저 일치시킨 뒤 NautilusTrader에서 짧게 실행합니다. 이어서 case 28의 sweep 없는 MTF continuation을 분리하고, 두 family를 동일 plan contract와 하나의 deterministic router 아래 연결합니다. 이후 fakeout reversal과 channel family를 추가합니다.

변경은 최근 성과를 설명하는 새 필터가 아니라 **특정 사례에서 확인된 source → interpretation → state → order 불일치를 고치는 경우**에 우선합니다.
