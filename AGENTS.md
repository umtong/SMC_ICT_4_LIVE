# 연구 AI 공통 안내

이 저장소는 연구 방법을 통제하지 않습니다. 반복 인프라를 제거해 목표 달성에 집중시키는 기반입니다.

1. 자신의 `research/*` 브랜치에서 목표 전체를 독립적으로 완성하십시오.
2. 다른 후보와 나중에 조립된다는 가정을 두지 마십시오.
3. NautilusTrader가 이미 제공하는 엔진·주문·회계·카탈로그를 다시 만들지 마십시오.
4. SMC/ICT 정의와 시나리오 설계는 자유롭게 연구하되 미래정보는 사용하지 마십시오.
5. 최소한 `smc4 doctor`, `smc4 smoke`, 공통 테스트가 계속 통과하게 하십시오.
6. 실행 결과에는 `run.json`과 시나리오 event log를 남겨 재현 가능하게 하십시오.

빠른 시작:

```bash
uv sync --locked
uv run smc4 doctor
uv run smc4 smoke --output artifacts/smoke
```

프로젝트의 목적과 금지사항은 `PROJECT_PRINCIPLES.md`, 기존 기능의 재사용 범위는 `REUSE_MAP.md`를 기준으로 합니다.
