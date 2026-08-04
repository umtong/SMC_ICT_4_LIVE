# 재사용 지도

이 문서는 “무엇을 만들 것인가”보다 “무엇을 다시 만들지 않을 것인가”를 먼저 고정합니다.

| 요구사항 | 사용 대상 | 결정 |
|---|---|---|
| 결정론적 이벤트 재생 | NautilusTrader backtest engine/node | 재구현하지 않음 |
| 주문 생명주기와 체결 이벤트 | NautilusTrader execution engine | 재구현하지 않음 |
| 포지션·계좌·포트폴리오 회계 | NautilusTrader portfolio/accounting | 재구현하지 않음 |
| 수수료·증거금·지연·체결 모델 | NautilusTrader venue/fill/latency 설정 | 가정을 명시하고 검증 |
| 데이터 저장·조회 | `ParquetDataCatalog` | 그대로 사용 |
| config 기반 백테스트 | `BacktestNode` | 공식 기준 경로 |
| 실거래 전환 | `TradingNode`와 동일 전략 구조 | 후속 단계에서 연결 |
| 병렬 연구 격리 | Git branch + worktree + 별도 프로세스 | 얇은 생성기만 제공 |
| 데이터 무결성 | SHA-256 data manifest | 프로젝트에서 얇게 추가 |
| 실행 재현성 | run manifest | 프로젝트에서 얇게 추가 |
| 미래정보 방지 | `event_time_ns`/`observed_time_ns` 계약 | 프로젝트 공통 안전장치 |
| SMC/ICT 사건 정의 | 프로젝트 고유 연구 | 각 후보가 독립 구현 |
| 시장 상태와 시나리오 상태 머신 | 프로젝트 고유 연구 | 각 후보가 독립 구현 |
| 진입·무효화·청산 인과관계 | 프로젝트 고유 연구 | 각 후보가 독립 구현 |
| 시나리오별 실패 원인 | 프로젝트 고유 연구 | 각 후보가 독립 진단 |

## 명시적으로 만들지 않는 것

- 자체 백테스트 엔진
- 자체 주문·포지션 회계
- 자체 메시지 버스
- 자체 분산 작업 스케줄러
- 연구 AI 승인·통제 워크플로
- 전 종목·전 연도 상시 실행 CI
- SMC/ICT 개념의 사전 정답 구현
- 웹 대시보드와 별도 실험 관리 서버

필요성이 실제 병목으로 입증되기 전에는 추가하지 않습니다.
