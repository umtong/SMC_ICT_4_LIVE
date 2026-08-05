# 공통 연구 기반

## 목적

공통 기반은 연구 후보가 시장 논리에 집중하도록 반복 작업만 제거합니다. 후보의 가설, SMC/ICT 정의, 파라미터, 상태 머신, 리스크 모델을 강제하지 않습니다.

## 제공하는 것

- Python·uv·NautilusTrader가 이미 설치된 GHCR 연구 이미지
- GitHub Codespaces와 Dev Container에서 이미지를 바로 여는 설정
- 정확히 고정된 Python/NautilusTrader 버전과 전체 의존성 lock
- 실제 NautilusTrader 엔진을 통과하는 결정론적 smoke test
- 사건 발생 시각과 관측 가능 시각을 구분하는 계약
- 상태 전이 event log와 검증기
- Git commit·branch·환경·설정·데이터 해시를 남기는 run manifest
- 데이터 파일 SHA-256 manifest 생성기
- 독립 branch/worktree 생성기
- GitHub Actions 기본 검증

## 연구 AI의 시작 상태

Codespace 또는 Dev Container를 열면 설치 과정은 없습니다. 다음 명령이 실행되면 공통 환경은 준비된 것입니다.

```bash
smc4 doctor
```

그 뒤 연구 AI는 환경을 다시 구성하지 않고 바로 목표 조사와 후보 구현으로 이동합니다. 각 브랜치의 `src`가 사전 설치된 기본 코드보다 우선되므로 후보 코드 변경은 즉시 반영됩니다.

## 제공하지 않는 것

- SMC/ICT 개념의 정답
- 전략 템플릿의 강제 구조
- 후보 간 코드 결합 절차
- 연구 승인 단계
- 수익률 최적화기
- 전체 데이터 상시 검증

## 검증 범위

공통 CI는 기반의 고장만 빠르게 찾습니다.

1. Python 모듈 컴파일
2. 단위 테스트
3. 버전·환경 doctor
4. 소형 합성 데이터로 주문 제출·체결·청산 smoke test
5. 게시된 연구 이미지를 새로 pull한 뒤 동일 검증 재실행

모든 종목과 모든 연도의 전략 검증은 성공 가능성이 확인된 연구 후보의 책임이며 상시 CI에 넣지 않습니다.
