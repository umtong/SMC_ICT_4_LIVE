# Source contract and cross-domain adaptations

## 자막에서 직접 고정한 규칙

- `-Tp2fhvVVGM` 05:40–09:20: 이전 반대 캔들 몸통을 현재 몸통이 전부 감싸면, 감싸진 몸통이 오더블록 zone. zone 재접촉 진입. 형성 캔들(두 캔들 구조)의 고점/저점 파괴가 기본 무효화. 이전 파동 고점/저점이 첫 유동성 목표.
- `F3exGqdN2Go` 04:00–07:10: zone 미접촉이면 추격하지 않음. 유동성 흡수·차트 구조 위치와 큰 몸통비는 품질 정보. 꼬리 극값 손절과 봉마감 손절 중 사용자는 즉시 stop-market 방식을 고정함. 첫 이전 파동 유동성에서 전량 종료하는 선택도 원문에 있음.
- `HReT0PtawRA` 02:00–05:00: 주요 고점·저점과 깨진 지지·저항의 손절 유동성을 먼저 찾고, 유동성만으로 진입하지 않으며 장악형 오더블록을 두 번째 근거로 사용.
- `F3exGqdN2Go` 17:00–21:10: 구조 밖 이탈 후 빠른 복귀(페이크아웃) 또는 지연 복귀(트랩). 확인 방식은 구조 안 마감 또는 복귀 뒤 리테스트. 무효화는 이탈 극값, 목표는 반대 구조.
- `CxVUB0E9OJU` 02:10–03:50: 추세선 돌파 뒤 첫 리테스트와 상승 오더블록 중첩. 이탈 시 추세선 역할전환과 좌측 유동성 논리가 함께 무효화.
- `V3kCjvJy3bg` 00:40–06:40: 큰 시간대는 context/zone, 15m·5m·1m은 실제 타점. 미마감 캔들은 오더블록이 아님. 여러 시간대 중첩은 품질 정보이나 현재 최소 구현에서는 강제 AND 필터로 쓰지 않음.

## 사용자가 원문보다 우선해 고정한 규칙

- 분할 진입·분할 익절·분할 손절 없음.
- single fixed target, full-position STOP_MARKET.
- 거래 수 제한·일일 손실 제한 없음.
- 비용 전 예상 손익비 1.0R 미만 거래 금지.
- 현재 NAV의 3% 계획손실 수량.

## 외부 분야에서 가져온 재사용 가능한 메커니즘

- 주문흐름 연구의 핵심은 “거래량” 자체보다 best-level 수급 불균형과 시장 깊이가 단기 가격 변화에 연결된다는 점이다. 초기 OHLC 후보가 살아남은 뒤, sweep의 acceptance/rejection을 구분하는 독립 관측으로만 추가한다.
- LOB resiliency 연구는 공격적 유동성 충격 뒤 가격 복원과 가격 지속이 서로 다른 상태임을 보여준다. 이것을 `SWEEP_RECLAIM_OB`와 `BREAK_ACCEPT_RETEST_OB`의 상위 분기로 사용한다.
- quickest-change detection의 교훈은 확인 조건을 무한히 AND하지 않고, 상태 변화의 누적 증거가 처음 충분해지는 시점에서 결정을 내리는 것이다. 후보는 interaction과 confirmation을 역할 분리하고 추가 확인봉을 기본값으로 넣지 않는다.
- event-based sampling의 교훈은 고정 시계열 패턴보다 의미 있는 가격 사건을 중심으로 상태를 갱신하는 것이다. pivot confirmation, sweep, break, reclaim, first retest가 사건 경계를 이룬다.

외부 요소는 소스 전략을 대체하지 않는다. OHLC 핵심 논리가 비용 후 살아남을 때만 order-flow/depth를 acceptance/rejection 판별에 추가한다.
