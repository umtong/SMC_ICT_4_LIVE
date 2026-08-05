# 연구 AI 공통 안내

이 저장소는 연구 방법을 통제하지 않습니다. 반복 인프라를 제거해 목표 달성에 집중시키는 기반입니다.

## 환경

GitHub Codespaces 또는 Dev Container로 시작했다면 Python, uv, NautilusTrader와 프로젝트 도구는 이미 설치되어 있습니다. `SMC4_PREBUILT_ENV=1`인 환경에서는 `pip install`이나 `uv sync`부터 시작하지 말고 바로 연구하십시오.

```bash
smc4 doctor
```

이 명령이 통과하면 환경 준비는 끝난 것입니다. 환경을 다시 만드는 대신 목표 달성에 필요한 조사·설계·구현·검증으로 이동하십시오.

## 연구

1. 자신의 `research/*` 브랜치에서 목표 전체를 독립적으로 완성하십시오.
2. 다른 후보와 나중에 조립된다는 가정을 두지 마십시오.
3. NautilusTrader가 이미 제공하는 엔진·주문·회계·카탈로그를 다시 만들지 마십시오.
4. SMC/ICT 정의와 시나리오 설계는 자유롭게 연구하되 미래정보는 사용하지 마십시오.
5. 실행 결과에는 `run.json`과 시나리오 event log를 남겨 재현 가능하게 하십시오.
6. 공통 기반의 이상이 의심될 때만 `smc4 smoke`와 공통 테스트를 사용하십시오.

새 독립 작업공간:

```bash
python scripts/new_research.py candidate-a
```

프로젝트의 목적과 금지사항은 `PROJECT_PRINCIPLES.md`, 기존 기능의 재사용 범위는 `REUSE_MAP.md`를 기준으로 합니다.
