# Candidate 06 — Post-Sweep Liquidity-Response Bifurcation v0.2

이 후보는 유동성 sweep 자체가 아니라 **sweep 뒤의 반응 순서**를 거래한다. 패턴 검출기는 이전 관측치로 형성된 유동성 수준과 현재 sweep만 기록하고, 상태기계는 그 다음 바들에서만 다음 경로를 판정한다.

```text
IDLE
→ UPPER/LOWER_SWEEP_RESPONSE_OBSERVATION
→ SRR_RESPONSE_CONFIRMED 또는 SAC_OBSERVATION
→ ENTRY_ARMED
→ ORDER_SUBMITTED
→ POSITION
→ TARGET / STOP / TIMEOUT / BOUNDARY_EXIT
```

- `SRR`: sweep 방향의 주문흐름이 가격을 유지하지 못하고 수준 reclaim와 반대 displacement가 확인된 경우.
- `SAC`: 수준 밖 acceptance와 같은 방향 displacement 뒤 첫 retest가 유지된 경우.
- 반응이 식별되지 않거나 충돌하면 `AMBIGUOUS → RESET`으로 거래하지 않는다.

## 고정 실행 조건

- 모든 성과 실행은 NautilusTrader `BacktestEngine`만 사용한다.
- BTCUSDT perpetual 1분봉, Binance 공개 USDT-M 선물 자료와 SHA-256 체크섬을 사용한다.
- 완성 봉은 `open_time + 1 minute`에만 관측 가능하다.
- 신호 확정 다음 완성 봉에서만 진입을 시도한다.
- 승인 거래당 계획 손실은 진입 직전 전체 NAV의 3%다.
- 진입·손절 수수료, 양방향 1 tick 불리한 체결을 수량 산정에 포함한다.
- maker/taker 모두 fill당 7 bps의 보수적 유효 비용을 적용한다.
- 신규 주문 또는 포지션은 동시에 하나만 허용한다.

## 단계적 검증

동결 주간은 다음 순서다.

1. 2024-02-26 UTC
2. 2024-09-23 UTC — 첫 주간 통과 시에만 개방
3. 2024-04-22 UTC — 앞선 두 주간 통과 시에만 개방

첫 주간에서는 고정된 인과 가설 우선순위로 baseline, SRR/SAC ablation, 흐름 proxy 제거, 선택성·coverage와 구조 기억 길이 변인을 각각 통제한다. 최대 수익 조합이 아니라 **전체 gate를 처음 통과한 인과 가설**만 잠근다. 세 주간이 모두 통과하기 전에는 장기평가를 실행하지 않는다.

```bash
smc4 doctor
python research/candidate-06/test_logic_v2.py
python research/candidate-06/run_matrix.py \
  --output artifacts/candidate-06/first-week-lab
```

주요 증거는 `artifacts/candidate-06/first-week-lab/`의 `matrix_summary.json`, 각 변형의 `metrics.json`, 거래·주문·포지션·NAV·상태 전이 파일에 기록된다.
