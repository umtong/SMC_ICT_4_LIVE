# candidate-easychart-v2

`쉽알남`의 표면 패턴을 많이 붙이지 않고, 사람이 자연스럽게 수행하는 핵심 판단을 하나의 경매 상태 전이로 압축하는 독립 후보입니다.

```text
의미 있는 경계
→ 경계 상호작용
→ 거부(REJECTION) / 수용(ACCEPTANCE) / 미해결
→ 첫 리테스트
→ 인과적 무효화
→ 가장 가까운 사전 존재 반대 유동성
```

## 고정 실행 계약

- 진입 직전 단일 continuous NAV의 3%가 계획손실예산입니다.
- 진입 전에 `ENTRY`, `STOP`, `TARGET`을 모두 확정합니다.
- 가장 가까운 사전 존재 목표 기준 비용 전 예상 손익비가 1.0R 이상일 때만 거래합니다. 더 먼 목표로 건너뛰지 않습니다.
- 한 번 전량 진입하고, 하나의 전량 `STOP_MARKET` 또는 하나의 전량 target으로 종료합니다.
- 분할 진입·분할 익절·분할 손절·본절 이동·트레일링·재량 조기청산을 사용하지 않습니다.
- 거래 수 제한, 하루 손실 제한, 연패 중단, 임의 cooldown을 두지 않습니다.
- BTCUSDT·ETHUSDT·SOLUSDT·XRPUSDT 전체에서 신규 진입 intent 또는 포지션은 최대 하나입니다.
- 주문·체결·수수료·포지션·계좌 NAV는 NautilusTrader가 처리합니다. 별도 matching/account simulator는 없습니다.
- Binance 1분 외부 봉만 거래소를 움직이며, 5분 신호 봉은 NautilusTrader의 composite internal bar로 집계합니다.

## v2의 돌파구 가설

기존 후보는 작은 고정-span pivot을 곧바로 유동성으로 취급하여 좁은 손절, 높은 비용/R, 과도한 명목노출을 만들었습니다. v2는 사람의 “의미 있는 고점·저점” 판단을 **다중 스케일 + ATR 정규화 topographic prominence**로 번역합니다. 이는 새 필터를 덧붙이는 것이 아니라 경계 자체의 정의를 고치는 변화입니다.

- pivot은 오른쪽 확인 구간이 모두 닫힌 뒤에만 관측됩니다.
- 같은 가격의 중첩 pivot은 가장 강한 한 경계로 접습니다.
- `REJECTION`: 경계 밖 excursion 뒤 종가가 안으로 복귀합니다.
- `ACCEPTANCE`: 몸통 종가가 밖에서 형성되고 다음 봉도 밖에서 시작·유지합니다.
- 첨 리테스트가 확인 전에 이미 지나갔다면 추격하지 않습니다.
- 손절은 작은 진입 zone이 아니라 sweep excursion 또는 돌파 causal origin 바깥입니다.

## 실행

```bash
smc4 doctor
PYTHONPATH=research/candidate-easychart-v2 \
python research/candidate-easychart-v2/run_backtest.py \
  --start 2024-02-01 --end 2024-02-14 --warmup-days 7 \
  --symbols BTCUSDT ETHUSDT SOLUSDT XRPUSDT \
  --cache .cache/candidate-easychart-v2 \
  --output artifacts/candidate-easychart-v2/dev
```
