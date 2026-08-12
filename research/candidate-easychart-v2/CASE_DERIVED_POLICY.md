# Case-derived EasyChart policy

이 문서는 도구별 전략 목록이 아니라 실제 영상 거래를 하나의 자동 의사결정으로 복원하기 위한 정책입니다. 근거는 `Project/cases`의 프레임과 같은 영상의 자막이며, 세부 연결은 `casebook_index.json`에 기록합니다.

## 1. 사례가 반복해서 보여 준 것

실제 거래는 `OB`, `FVG`, 추세선, 채널, Fake out 중 하나를 매번 고르는 방식도 아니고, 전부를 `AND`로 요구하는 방식도 아니었습니다. 거래마다 필요한 문제를 서로 다른 도구가 해결했습니다.

```text
context
→ boundary / objective
→ interaction
→ rejection / acceptance / continuation / unresolved
→ executable location or confirmation
→ causal invalidation
→ pre-existing objective
```

- 추세선·채널·수평 구조는 방향과 사람들이 학습한 경계를 주로 제공했습니다.
- Fake out/Trap은 독립 zone이 아니라 경계 밖 가격의 수용 실패라는 상태 전이였습니다.
- OB는 causal origin, 실행 구간, 반대 저항/목표 중 하나의 역할을 맡았습니다.
- FVG는 displacement 흔적, 되돌림 실행 구간, S/R flip과의 중첩, 반대 목표 중 하나의 역할을 맡았습니다.
- 동종 또는 타임프레임이 다른 구조의 중첩은 근거 개수 합산이 아니라 위치의 의미를 보완했습니다.
- BTC·ETH·USDT.D 같은 peer 구조는 일부 사례에서 방향 router의 보조 정보였지만 모든 거래의 필수조건은 아니었습니다.

## 2. 반복된 완전한 scenario family

### A. `BREAK_FIRST_RETEST_CONFLUENCE`

```text
관측 가능한 추세선/구조 경계
→ 방향성 있는 close break
→ 첫 retest
→ retest에서 OB·S/R·다른 구조가 실행 위치를 제공
→ retest/formation low-high 무효화
→ 다음 반대 유동성
```

- case 02와 case 14에서 명시적으로 반복됩니다.
- 두 사례 모두 별도 local swing sweep을 필수로 요구하지 않았습니다.
- 따라서 현재 `MTF_ZONE_SWEEP_FIRST_5M_OB`의 sweep은 그 family 안의 한 변형일 수는 있어도 EasyChart 전역 필수조건이 아닙니다.

### B. `MTF_ZONE_CONTINUATION`

```text
진행 중인 방향
→ 서로 다른 시간대의 같은-side OB/FVG 또는 구조 중첩
→ 해당 zone으로의 되돌림
→ zone 유지 또는 하위 반응
→ formation 구조 무효화
→ 직전 고점/저점 또는 반대 zone
```

- case 28은 15분과 1시간 상승 장악형 OB 중첩을 진입 이유로 직접 기록했습니다.
- 이 사례에도 별도 sweep은 명시되지 않았습니다.

### C. `BREAK_ACCEPT_RETEST_CONTINUATION`

```text
저항/지지 돌파와 바깥 가격 수용
→ S/R flip
→ FVG·OB 등과 겹친 되돌림 구간
→ causal leg 저점/고점 무효화
→ 다음 반대 구조
```

- case 21에서 FVG와 S/R flip이 실행 위치, 작은 상승 파동 저점이 손절, 상단 OB가 목표 역할을 맡았습니다.

### D. `LIQUIDITY_FAKEOUT_REVERSAL`

```text
학습된 경계 또는 pattern neckline
→ 경계 밖 유동성 excursion
→ 내부 복귀 / acceptance 실패
→ retest 또는 반대-side 구조
→ excursion extreme 무효화
→ 반대 구조
```

- case 30과 case 34는 추세선·넥라인·채널·OB/FVG가 같은 thesis에서 각기 다른 역할을 맡았습니다.
- 여러 진입 band는 여러 전략이 아니라 하나의 causal episode였습니다.

### E. `BREAKDOWN_ACCEPTANCE_CONTINUATION`

```text
반복 접촉으로 학습된 추세선·쐐기·채널 경계
→ 방향성 close break
→ 바깥 가격 유지 또는 retest
→ OB/FVG가 실행 위치를 보완
→ 경계 재진입 또는 causal high-low 무효화
→ 다음 S/R·유동성
```

- case 35에서 여섯 번 이상 접촉한 추세선, 쐐기 하단 돌파, 4시간 bearish OB가 함께 사용됐습니다.

### F. `CHANNEL_ROTATION_OR_FAILURE`

채널은 최소 세 점으로 관측된 뒤 네 번째 상호작용, 이탈·재진입, 강한 돌파를 서로 다른 상태로 route해야 합니다. 아직 실제 프레임 전체의 anchor 일치 검토와 detector 구현이 남아 있으므로 현 성과로 평가하지 않습니다.

## 3. 단일-entry 프로젝트로 번역하는 규칙

영상의 분할진입·물타기·부분익절을 흉내 내지 않습니다. 그러나 여러 band가 등장했다는 이유로 근거를 버리지도 않습니다.

1. 여러 band가 같은 invalidation과 objective를 공유하면 하나의 thesis로 묶습니다.
2. 각 band가 독립적으로 `entry-stop-target`과 gross `RR >= 1.0`을 만족하는지 사전에 계산합니다.
3. 단일-entry 선택법은 결과를 본 뒤 가장 좋은 band를 고르는 방식으로 정하지 않습니다.
4. 우선 비교할 두 source-faithful 번역은 다음뿐입니다.
   - **first executable band:** 사전 계획한 band 중 처음 도달하고 완성된 하나
   - **first confirmed band:** state transition 뒤 처음 확인이 완성된 하나
5. 같은 causal event에서 하나가 선택되거나 놓치면 다른 band로 재진입하지 않습니다.
6. 두 번역 중 어느 것이 실제 사례 geometry와 비용 후 경로를 더 잘 보존하는지는 동일 사례와 untouched 구간에서 비교합니다.

## 4. 도구 검증과 시스템 검증의 경계

도구 detector는 실제 프레임의 위치·관측 시점과 맞는지 개별 확인합니다. 그러나 도구별 손익을 최종 알파로 해석하지 않습니다.

```text
부품 검사: 사람이 그린 OB/FVG/line/channel과 코드 객체가 같은가?
family 검사: 완전한 state transition과 entry geometry가 같은가?
최종 검사: 모든 family가 단일 router·단일 포지션·continuous NAV에서 작동하는가?
```

## 5. 현재 코드에 대한 정확한 위치

- `easychart_zones.py`는 source-defined OB/FVG geometry를 causal하게 만드는 부품입니다.
- `easychart_mtf_scenario.py`는 `MTF_ZONE_SWEEP_FIRST_5M_OB`라는 한 family 가설입니다.
- `causal_trendlines.py`는 break/retest/fakeout family가 공유할 wick trendline lifecycle 부품입니다.
- 어느 하나도 단독으로 EasyChart 전체 시스템이라고 부르지 않습니다.

## 6. 다음 구현 순서

1. `causal_trendlines.py`의 line anchor와 break/retest가 case 02·14·35의 프레임과 맞는지 확인합니다.
2. case 02·14의 `BREAK_FIRST_RETEST_CONFLUENCE`를 가장 작은 완전한 family로 구현합니다.
3. 현재 MTF family에서 local sweep이 없는 case 28 변형을 별도 상태로 복원합니다.
4. 두 family를 동일 plan contract와 하나의 deterministic router로 연결합니다.
5. 이후 fakeout reversal과 channel family를 추가합니다.
6. 성과가 아니라 사례 불일치가 발견될 때만 detector 또는 state logic을 수정합니다.
