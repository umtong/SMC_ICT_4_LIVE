# SMC/ICT 4호기 — Research Foundation

이 저장소는 연구 AI를 통제하기 위한 절차 시스템이 아닙니다. 각 연구 AI가 **동일한 목표 전체를 독립적으로 시도**할 때 반복해서 만들 필요가 없는 공통 기반을 제공합니다.

- 백테스트·주문·회계·이벤트 재생은 NautilusTrader를 사용합니다.
- 각 `research/*` 브랜치는 다른 브랜치와 결합하지 않는 하나의 완성 후보입니다.
- 성공하고 검증된 후보만 `main`에 승격합니다.
- 공통 기반은 연구 방법을 정하지 않습니다. SMC/ICT 정의와 시나리오 논리는 각 후보의 연구 대상입니다.

## 시작

필수 환경은 Python 3.13, `uv`, Git입니다.

```bash
uv sync --locked
uv run smc4 doctor
uv run smc4 smoke --output artifacts/smoke
uv run python -m unittest discover -s tests -p 'test_*.py'
```

새 독립 연구 작업공간은 다음 한 명령으로 만듭니다.

```bash
uv run python scripts/new_research.py candidate-a
```

기본값은 현재 저장소와 나란히 `worktrees/candidate-a`를 만들고, `research/candidate-a` 브랜치를 생성합니다.

## 저장소 지도

```text
PROJECT_PRINCIPLES.md       프로젝트의 연구 목적과 우선순위
REUSE_MAP.md                NautilusTrader 재사용 범위와 직접 연구 범위
AGENTS.md                   새 연구 AI가 읽을 짧은 공통 안내
src/smc_ict_4/contracts.py  시장 사건의 발생 시각과 관측 가능 시각 계약
src/smc_ict_4/event_log.py  시나리오 상태 전이 기록·검증
src/smc_ict_4/manifest.py   코드·설정·데이터·환경 실행 기록
src/smc_ict_4/smoke.py      실제 NautilusTrader 주문 경로 smoke test
scripts/new_research.py     독립 브랜치/worktree 생성
research/_template          연구 후보의 최소 시작점
tests                       공통 기반의 빠른 검증
```

## 고정 기반

- Python: `3.13`
- NautilusTrader: `1.230.0`
- 시간 기준: UTC / Unix nanoseconds
- 큰 시장 데이터와 실행 산출물: Git에 커밋하지 않음
- 데이터는 파일 해시와 manifest로 식별

NautilusTrader 버전 변경은 개별 연구 브랜치가 아니라 공통 기반 변경으로 처리합니다.
