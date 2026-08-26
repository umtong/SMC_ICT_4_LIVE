# EasyChart v2 understanding contract

이 문서는 성과 보고서가 아니라 **무엇을 이해했고 무엇을 아직 구현하지 않았는지**를 고정하는 연구 계약입니다. 구현하지 않은 개념의 효과를 백테스트 결과로 판단하지 않습니다.

## 1. 자료에서 확인된 전체 의사결정 구조

EasyChart의 매매는 `OB`, `FVG`, `추세선`, `채널`, `Fake out`을 독립 패턴으로 각각 매수하는 방식이 아닙니다.

```text
상위 시간대 방향·주요 지지/저항
→ 현재 가격이 향하는 유동성/목표
→ 의미 있는 구조 또는 겹치는 구간
→ 그 구간과 가격의 상호작용
→ 거부·수용·미해결 상태 판별
→ 하위 시간대 진입 확인
→ 동일 파동의 무효화와 반대 구조 목표
→ 목표 구간이 오지 않으면 NO TRADE
```

### 시간대별 역할

- 월봉·주봉·일봉: 큰 추세와 주요 지지/저항
- 12시간·4시간·1시간: 중간 추세, 패턴, 거래 구간
- 15분·5분·1분: 실제 진입 타이밍
- 한 시간대의 신호를 다른 시간대에서 반복 확인하는 것이 아니라, 시간대마다 서로 다른 역할을 맡깁니다.

### 구조물별 역할

- **오더블럭:** 모든 반대색 캔들이 아니라 유동성 흡수 또는 의미 있는 구조에서 출발한 몸통 구간입니다. 가격이 돌아오지 않으면 진입하지 않습니다.
- **FVG:** 모든 3캔들 갭이 아니라 눈에 띄는 변위가 만든 불균형입니다. 유동성 흡수·OB·파동 맥락과 결합하며 생성 파동의 되돌림 수명 안에서 사용합니다.
- **추세선:** 진입 신호라기보다 방향, 이동 속도, 대중이 보는 유동성 경계를 제공합니다. 의미 있는 wick 고저점을 연결하고 bounce 또는 break-and-retest 상태를 구분합니다.
- **채널:** 최소 세 점으로 확인된 평행 경계와 반대편 목표를 제공합니다. 4포인트 반전, 이탈 후 retest, 강한 돌파를 서로 다른 상태로 다룹니다.
- **Fake out / Trap:** `축적 → 구조 밖 유동성 흡수 → 내부 복귀/안착 실패 → retest → 반대 방향 이동`이라는 상태 전이입니다. OB/FVG/추세선/채널이 어디에서 이 전이가 의미 있는지를 결정합니다.

## 2. 현재 코드가 실제로 구현한 것

- Binance 1분 외부 봉과 NautilusTrader 5분 내부 합성 봉
- 다중 span·ATR prominence 기반의 causal pivot 경계
- pivot 경계 sweep/reclaim 후 확인과 첫 retest
- pivot 경계의 body break 후 다음 봉 outside hold와 첫 retest
- source·origin·target을 같은 auction scale 이상으로 제한
- 네 종목 단일 continuous account, 전역 한 포지션, NAV 3% 위험수량
- NautilusTrader bracket, 체결, 수수료, 포지션, 계좌 처리
- 모든 plan, arbitration, order tag, fill, trade window를 추적하는 감사 경로

이것은 **generic causal boundary baseline**입니다. EasyChart 전체 전략의 구현이라고 부르지 않습니다.

## 3. 아직 구현하지 않은 것

| 구성 요소 | 상태 | 현재 결과로 효과 판단 가능 여부 |
|---|---|---|
| 월/주/일 및 12h/4h/1h 방향·구간 | 미구현 | 불가 |
| 15m/5m/1m 역할 분리 | 미구현 | 불가 |
| 외부 유동성 목표와 내부 PD-array 구분 | 부분 개념만 존재 | 불가 |
| EasyChart 정의의 OB zone | 미구현 | 불가 |
| EasyChart 정의의 FVG zone와 수명 | 미구현 | 불가 |
| wick 기반 추세선과 bounce/break routing | 미구현 | 불가 |
| 완전 평행 채널, 3점 확인, 4포인트 상태 | 미구현 | 불가 |
| contraction·반복 접촉으로 학습된 유동성 | 미구현 | 불가 |
| 하위 시간대 반전/돌파-retest 확인 | 미구현 | 불가 |
| 자료의 부분 익절·반익반본 관리 | 프로젝트 공통 계약상 의도적으로 미사용 | 별도 비교 필요 |

따라서 현재 손실 결과는 다음만 반증할 수 있습니다.

> `5분봉 generic prominence pivot을 곧바로 거래 가능한 유동성으로 간주하는 정책`은 현재 비용 구조에서 약하다.

다음과 같은 결론은 내릴 수 없습니다.

- OB가 효과 없다.
- FVG가 효과 없다.
- 추세선이나 채널이 효과 없다.
- Fake out/Trap 전체 전략이 효과 없다.
- EasyChart 방식이 효과 없다.

그 개념들은 아직 자료의 정의대로 구현·검증되지 않았기 때문입니다.

## 4. 구현 오류와 이해 오류를 분리하는 감사 기준

각 완료 거래는 다음 질문에 답할 수 있어야 합니다.

1. 상위 시간대 context와 목표 유동성은 신호 전에 무엇이었는가?
2. source 구조는 언제 형성됐고 언제 관측 가능해졌는가?
3. OB/FVG/추세선/채널 중 어떤 역할의 구조였는가?
4. interaction, confirmation, entry는 각각 다른 관측으로 성립했는가?
5. 계획 entry와 실제 fill은 언제 얼마에 체결됐는가?
6. stop과 target은 같은 auction leg에 속했는가?
7. 신호 후 첫 retest가 실제로 남아 있었는가?
8. 다른 종목·scenario와 충돌해 거절된 신호는 무엇인가?
9. 손실은 논리 실패인가, 체결/수수료/슬리피지 실패인가, 구현 오류인가?
10. 해당 장면을 사람이 자료의 규칙으로 보았을 때도 실제로 거래했을 것인가?

`trade_audit.csv`, `decision_events.csv`, `trade_windows.jsonl`, 원본 NautilusTrader reports가 이 검사를 위한 최소 증거입니다.

## 5. 다음 구현의 기준

다음 후보는 threshold를 더 붙인 pivot 전략이 아니라 아래의 한 완전한 scenario를 먼저 구현합니다.

```text
1h/4h context와 외부 유동성 objective
→ 15m 의미 있는 liquidity event
→ 그 event에서 생성된 OB/FVG zone
→ 5m/1m zone interaction 및 거부/수용 확인
→ 사전에 정한 entry·invalidation·동일 파동 target
→ exact fill audit
```

이 scenario의 거래가 자료의 그림과 자막에서 의도한 장면에 대응하는지 먼저 확인한 뒤에만 성과를 해석합니다. 이후 채널 4포인트, 채널/추세선 break-retest, 다른 독립 scenario를 추가하여 통합 opportunity set을 넓힙니다.
