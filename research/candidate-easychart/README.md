# candidate-easychart

`쉽알남` 자료에서 반복되는 **유동성 위치 → 몸통 장악형 오더블록 → 첫 재접촉 → 구조적 무효화 → 반대 유동성** 흐름을 기계화하는 독립 후보다.

## 사용자 고정 계약

- 현재 continuous NAV의 3%가 거래당 계획 손실예산이다.
- 진입 전에 비용 전 단일 목표 손익비가 1.0R 이상이어야 한다.
- 한 번 진입하고, 하나의 full-position `STOP_MARKET` 또는 하나의 full-position target으로 종료한다.
- 분할 진입·분할 익절·분할 손절·본절 이동·트레일링·재량 조기청산을 사용하지 않는다.
- 거래 수 제한, 일일 손실 제한, 연패 중단, 임의 cooldown을 두지 않는다.
- BTCUSDT·ETHUSDT·SOLUSDT·XRPUSDT 전체에서 신규 진입 intent 또는 포지션은 최대 하나다.

## 현재 시나리오

1. `SWEEP_RECLAIM_OB`: 확인된 고점·저점 유동성 이탈과 복귀 뒤 EasyChart body-engulfing OB, 첫 재접촉 진입.
2. `BREAK_ACCEPT_RETEST_OB`: 확인된 경계 밖 body close와 EasyChart OB 뒤 첫 역할전환 재접촉 진입.
3. `SWEEP_RECLAIM_RETEST`: 자막의 페이크아웃/트랩 원형대로 구조 안 복귀 후 첫 재접촉. OB를 추가 조건으로 강제하지 않는 독립 family.
4. `BREAK_ACCEPT_RETEST`: 경계 밖 수용 뒤 첫 역할전환 재접촉. OB 중첩 없는 원형을 독립 진단.

목표는 항상 **가장 가까운 아직 소모되지 않은 반대 유동성**이다. 그 첫 목표가 1.0R 미만이면 더 먼 목표로 건너뛰지 않고 거래 자체를 거절한다. 60분 확인 구조는 선택적 방향 router로만 쓰며 5분 trigger를 반복 확인하지 않는다.

EasyChart OB는 일반 ICT의 “마지막 반대 캔들”을 그대로 쓰지 않는다. 이전 반대 캔들의 **몸통 전체**를 현재 몸통이 감싸면, 감싸진 이전 몸통이 zone이다. 구조는 현재 캔들 마감 뒤에만 알려진다.

## 실행 순서

```bash
smc4 doctor
python -m unittest discover -s research/candidate-easychart/tests -p 'test_*.py' -v
python research/candidate-easychart/screen.py \
  --start 2024-02-01 --end 2024-02-28 \
  --cache .cache/candidate-easychart \
  --output artifacts/candidate-easychart/dev-2024-02
```

`screen.py`는 약한 논리를 싸게 폐기하기 위한 진단기일 뿐이다. 성과 승격은 NautilusTrader single continuous account 결과만 인정한다.
