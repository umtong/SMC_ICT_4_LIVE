# Windows 11 재현·검증·Shadow/Paper 운용

이 문서는 `production-candidate-liquidity-episode-policy-v1`의 실제 운용 경로다.
채팅 첨부물이나 임시 파일이 없어도 저장소만 clone하면 같은 소스·설정·상태계약으로
복원, 역사 평가, 연결형 shadow, NautilusTrader paper를 실행할 수 있게 구성했다.

## 1. 무엇이 복원됐고 무엇이 새로 추가됐는가

복원된 알파 소스는 변경하지 않는다.

```text
route_episode_policy.py Git blob
92459a08e98a634ec0a096ec1d567c78abdff7a9

historical source commit
8ec7bbc6c6f29b0bae5b2d386106056ca8697d4e
```

`production/`은 이 소스를 둘러싼 운영 어댑터다.

- Binance USD-M Futures의 완료된 1분 futures/mark/index bar와 공개 포지셔닝 자료만 저장한다.
- 현재 진행 중인 bar는 exchange server time 기준으로 제거한다.
- 기존 `episode_policy.generate_symbol`을 그대로 호출한다.
- outcome, fill, MFE/MAE, resolution 같은 미래 라벨은 live 입력에서 제거한다.
- frozen model bundle이 없거나 충분한 성숙 라벨로 만들어지지 않았으면 order-capable mode는 시작하지 않는다.
- 네 종목이 하나의 SQLite account slot을 공유한다.
- 실제 주문 수량은 현재 paper equity, 구조적 stop 거리, 3% risk fraction, 3x account leverage cap으로 결정한다.
- entry는 GTD limit, exit는 사전 고정된 TP/SL bracket이다.

## 2. 세 운용 모드

### Shadow

`configs/shadow.windows.json`

공개시장에 연결해 실제 정책을 계산하지만 주문 제출 코드가 존재하지 않는다.
PowerShell 실행기는 `BINANCE_API_KEY` 또는 `BINANCE_API_SECRET`이 보이면 즉시 중단한다.
결정은 `OBSERVED`로만 저장된다.

### Paper

`configs/paper.windows.json`

Binance live public data adapter와 NautilusTrader `SandboxExecutionClient`를 결합한다.
거래소 계정·API key 없이 live market에 대한 가상 margin account, order lifecycle,
부분체결/거절 이벤트 처리, portfolio/account report를 사용한다. 실행 가능한 결정은
성숙한 역사자료로 만든 model bundle이 있을 때만 `READY`가 된다.

### Binance Futures testnet

`configs/testnet.windows.example.json`

NautilusTrader Binance USD-M Futures testnet execution adapter를 사용한다. 다음 세 조건을
모두 만족해야 시작한다.

1. paper model bundle 존재
2. `BINANCE_API_KEY`, `BINANCE_API_SECRET`이 testnet credential
3. `Run-Testnet.ps1 -IUnderstandThisSubmitsTestnetOrders`

실자금 Binance production endpoint를 사용하는 실행 명령은 이 후보에 포함하지 않았다.

## 3. Windows 11 최초 설치

PowerShell 7 또는 Windows PowerShell을 열고 저장소 루트에서 실행한다.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\research\candidate-liquidity-episode-policy-v1\windows\Bootstrap.ps1
.\research\candidate-liquidity-episode-policy-v1\windows\Verify.ps1
```

`Bootstrap.ps1`은 다음을 수행한다.

- Windows build 22000 이상 확인
- Python 3.13 native venv 생성
- root project와 `nautilus_trader==1.230.0` 설치
- scikit-learn/joblib 고정 버전 설치
- restored blob, strict causal router, source import, SQLite restart/hash-chain self-check

`Verify.ps1`은 추가로 실제 NautilusTrader `BacktestEngine`에서 limit-entry + TP/SL bracket을
체결하고 position close 후 global account slot이 해제되는지 확인한다.

증거는 candidate 아래 `runtime/bootstrap`, `runtime/verification`에 남는다.

## 4. 역사 continuous 재현과 model bundle

주문 가능한 paper는 heuristic fallback을 사용하지 않는다. 먼저 고정된 development 기간에서
fill probability와 target-before-stop probability 모델을 만든다. 라벨은 fill/cancel 또는 TP/SL
결과가 development cutoff 전에 실제로 관측 가능했던 행만 사용한다.

```powershell
.\research\candidate-liquidity-episode-policy-v1\windows\Build-Model.ps1 `
  -Start 2024-01-01 `
  -DevelopmentEnd 2024-10-01 `
  -End 2025-01-01
```

이 명령은 월별로 데이터를 내려받아도 최종 라우팅은 네 종목 하나의 global account에서
시간순으로 한 번 수행한다. 월별 성과를 따로 계산해 더하지 않는다.

더 긴 고정 구간은 다음처럼 실행한다.

```powershell
.\research\candidate-liquidity-episode-policy-v1\windows\Run-Historical-Continuous.ps1 `
  -Start 2024-01-01 `
  -DevelopmentEnd 2025-01-01 `
  -End 2026-08-01 `
  -Name long-2024-2026
```

핵심 결과 위치:

```text
runtime/historical/<name>/harvest/
runtime/historical/<name>/account/
runtime/historical/<name>/continuous_manifest.json
runtime/model/model_bundle.joblib
runtime/model/model_bundle.joblib.json
```

## 5. Connected shadow

API key가 없는 새 PowerShell에서 실행한다.

```powershell
Remove-Item Env:BINANCE_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:BINANCE_API_SECRET -ErrorAction SilentlyContinue
.\research\candidate-liquidity-episode-policy-v1\windows\Run-Shadow.ps1
```

짧은 연결 진단:

```powershell
.\research\candidate-liquidity-episode-policy-v1\windows\Run-Shadow.ps1 -DurationSeconds 600
```

재시작 검증은 같은 명령을 다시 실행하면 된다. 기존 SQLite를 열어 integrity, event hash chain,
마지막 policy bucket, market cache, episode IDs를 검증한 뒤 중복 없이 계속한다.

## 6. Nautilus sandbox paper

model bundle을 만든 뒤 실행한다.

```powershell
.\research\candidate-liquidity-episode-policy-v1\windows\Run-Paper.ps1
```

foreground supervisor가 두 process를 관리한다.

```text
producer
  public REST state -> restored policy -> frozen models -> one account decision

nautilus execution node
  Binance live data -> SandboxExecutionClient -> protected bracket -> events/reconciliation
```

한 process가 비정상 종료하면 supervisor는 다른 process도 종료한다. 이를 통해 producer만 살아서
결정을 계속 쌓거나 execution node만 남아 stale decision을 집행하는 상태를 방지한다.

## 7. 상태와 reconciliation

```powershell
.\research\candidate-liquidity-episode-policy-v1\windows\Status.ps1 -Mode shadow
.\research\candidate-liquidity-episode-policy-v1\windows\Status.ps1 -Mode paper
```

직접 reconciliation:

```powershell
$python = ".\research\candidate-liquidity-episode-policy-v1\.venv\Scripts\python.exe"
Push-Location .\research\candidate-liquidity-episode-policy-v1
& $python -m production.cli reconcile --database runtime\episode-policy-paper\runtime.sqlite3
Pop-Location
```

검증 대상:

- SQLite `PRAGMA integrity_check`
- append-only event chain의 `previous_hash -> event_hash`
- decision 상태와 account slot의 일치
- 중복 episode/decision ID
- producer process lease
- live source의 마지막 완료 bar

## 8. 장애 시 행동

- 네트워크/API 오류: 해당 cycle을 실패로 기록하고 주문 결정을 만들지 않는다.
- mark/index/futures 자료 공백이 허용치를 넘음: 정책 계산을 중단한다.
- model bundle 없음/손상: paper/testnet 시작 거부.
- order denied/rejected: decision을 terminal로 만들고 account slot 해제.
- entry expiry 도과: 주문 제출 없이 `EXPIRED`.
- process restart: event chain과 checkpoint를 검증한 뒤 시작.
- 현재 portfolio가 flat이 아님: 새 account-wide decision을 claim하지 않는다.
- shutdown: open order 취소. `close_positions_on_stop`은 기본 false라서 임의 시장가 청산을 만들지 않는다.

## 9. GitHub Actions와 로컬의 관계

- Windows workflow: native wheel 설치, source contract, unit tests, Nautilus bracket smoke.
- connected-shadow workflow: 공개시장 연결, 별도 두 phase 재시작, final reconciliation.
- paper-connectivity workflow: Binance live data + Nautilus sandbox node 연결과 synthetic bracket lifecycle.
- historical/model workflows: 큰 public data 작업이므로 명시적 dispatch.

Actions 성공은 해당 SHA의 설치·실행·복구 근거다. 전략 성과는
`account/`의 하나의 continuous account 결과로 직접 판단해야 하며 workflow 이름으로 대체하지 않는다.
