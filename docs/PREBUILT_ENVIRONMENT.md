# 사전 구축 연구 환경

## 목적

새 연구 AI가 설치·의존성 충돌·NautilusTrader 동작 확인에 시간을 쓰지 않고 즉시 목표 연구를 시작하도록 합니다. 이 환경은 연구 방법을 정하거나 후보를 통제하지 않습니다.

## 실행 단위

GitHub Container Registry에 다음 이미지를 게시합니다.

```text
ghcr.io/umtong/smc-ict-4-live-research:foundation-1.0.0
```

이미지에는 Python 3.13, uv 0.11.25, 잠금 파일 전체 의존성, NautilusTrader 1.230.0, 프로젝트 CLI와 공통 라이브러리가 들어 있습니다. `/opt/smc4/.venv`는 `vscode` 사용자가 읽고 수정할 수 있어 연구 후보가 추가 라이브러리를 실험하는 것을 막지 않습니다.

## 연구 브랜치 연결

`.devcontainer/devcontainer.json`은 이미지를 빌드하지 않고 바로 가져옵니다. 작업 브랜치의 `src`가 `PYTHONPATH`에서 우선되므로, 공통 이미지에 설치된 기본 코드보다 현재 연구 브랜치의 코드가 사용됩니다.

따라서 다음 두 조건을 동시에 만족합니다.

1. Python과 NautilusTrader 등 무거운 공통 의존성은 설치되어 있음
2. 각 연구 브랜치의 코드 변경은 즉시 반영됨

## 게시 검증

`.github/workflows/research-image.yml`은 공통 런타임 파일이 바뀔 때 이미지를 다시 만듭니다. 게시 후 태그만 신뢰하지 않고 게시된 digest를 새로 pull하여 다음을 확인합니다.

```text
Python 실행
NautilusTrader 1.230.0 import
smc4 doctor
합성 BTCUSDT 주문 제출·체결·청산 smoke
run.json 생성
```

모두 통과한 이미지에만 `foundation-1.0.0`과 `latest` 태그를 게시합니다. 검증 결과와 정확한 digest는 GitHub Actions artifact에 보존합니다.

## 연구 AI의 시작점

Codespace 또는 Dev Container가 열리면 설치 명령은 필요하지 않습니다.

```bash
smc4 doctor
python scripts/new_research.py candidate-a
```

`SMC4_PREBUILT_ENV=1`은 현재 세션이 사전 구축 환경임을 나타냅니다.

## 경계

이미지는 공통 실행 환경만 제공합니다. 다음은 각 독립 후보가 연구합니다.

- SMC/ICT 개념의 기계적 정의
- 시장 상태와 시나리오 상태 머신
- 진입·무효화·청산 논리
- 위험 관리
- 실제 데이터 선택과 연구 검증

환경은 연구의 출발 마찰을 제거하지만 연구 결과를 미리 정하지 않습니다.
