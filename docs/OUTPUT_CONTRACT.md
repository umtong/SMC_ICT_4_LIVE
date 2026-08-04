# 연구 출력 계약

공통 기반이 요구하는 출력은 연구 보고서 양식이 아니라 재현성에 필요한 최소 사실입니다.

```text
artifacts/<run_id>/
├── run.json
├── metrics.json            # 후보가 자유롭게 정의 가능
├── scenario_events.jsonl   # 권장
├── orders.csv              # Nautilus smoke/후보 선택
├── positions.csv           # Nautilus smoke/후보 선택
└── errors.log              # 오류가 있을 때
```

`run.json`은 다음을 자동 기록할 수 있습니다.

- run ID와 UTC 시작 시각
- branch와 commit
- working tree dirty 여부
- Python/platform/NautilusTrader 버전
- 설정 파일 SHA-256
- 데이터 manifest SHA-256
- 후보가 추가한 metadata

`scenario_events.jsonl`은 각 후보가 상태 전이를 설명할 때 사용하는 공통 최소 형식입니다. 연구 후보는 필요한 필드를 `details`에 자유롭게 추가할 수 있습니다.
