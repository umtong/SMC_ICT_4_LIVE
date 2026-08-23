# Windows 11 execution

## Prerequisites

- Git for Windows
- official CPython 3.13 x64 with the `py` launcher
- outbound HTTPS/WebSocket access to Binance public APIs for connected checks
- Docker Desktop only when `run-all.ps1 -BuildDocker` or Compose is used

Neither `shadow` nor `sandbox` needs an API key.  Never store testnet keys in
the repository.

## Clone, test and connect in one command

```powershell
git clone https://github.com/umtong/SMC_ICT_4_LIVE.git
Set-Location .\SMC_ICT_4_LIVE
git switch codex/liquidity-synthesis-v2

Set-ExecutionPolicy -Scope Process Bypass
.\research\candidate-liquidity-episode-policy-v1\production_candidate\windows\run-all.ps1
```

`run-all.ps1` creates `.venv`, installs the project with its directly pinned
NautilusTrader 1.230.0 plus pinned pytest, runs the entire `tests` directory,
builds a Nautilus live node, runs a bounded 60-second
public-market shadow connection, and prints the resulting SQLite status.  Use
`-ConnectedMode sandbox -ConnectedSeconds 180` for the paper label, or
`-BuildDocker` to build the shipped image as well.

This command establishes software contracts, public connectivity and durable
state integrity.  A short connected run may produce no trade and is not a
strategy-performance result.

The same steps can be run separately:

```powershell
$Pc = ".\research\candidate-liquidity-episode-policy-v1\production_candidate\windows"
& "$Pc\bootstrap.ps1"
& "$Pc\verify.ps1"
& "$Pc\run-shadow.ps1"
```

Stop an unbounded connected process with `Ctrl+C`.  Inspect or back up a flat,
stopped runtime with:

```powershell
& "$Pc\status.ps1" -Mode shadow
& "$Pc\backup.ps1" -Mode shadow
```

SQLite policy and event evidence are durable, but the in-process sandbox account
has no external durable backing.  Once an order is accepted or filled, the state
file is intentionally non-restartable even if the strategy later becomes flat.
Inspect `status.ps1 -Mode shadow` or `sandbox`, archive the database, then choose
a new state path instead of claiming native account recovery.  The shipped
Compose services therefore use `restart: "no"`.  Testnet restart relies on
exchange-report reconciliation and has a different boundary.

## Native long continuous replay

Historical replay reads local official Binance Vision archives; it does not
download missing months.  A monthly root is the directory commonly ending in
`raw\binance\futures_um\monthly` and must contain canonical `klines`,
`fundingRate` and `markPriceKlines` trees.

Inventory evidence is optional but explicit.  `-MetricsRoot` points to the
official Binance Vision USD-M `daily\metrics` root containing
`<SYMBOL>\<SYMBOL>-metrics-YYYY-MM-DD.zip` and its `.CHECKSUM` file for every
required day.  The preceding UTC day is also required for causal warmup.

For one root:

```powershell
& "$Pc\verify-long-continuous.ps1" `
  -MonthlyRoot "D:\market-data\raw\binance\futures_um\monthly" `
  -MetricsRoot "D:\market-data\raw\binance\futures_um\daily\metrics"
```

For a primary archive plus a non-overlapping supplemental root:

```powershell
& "$Pc\verify-long-continuous.ps1" `
  -MonthlyRoot @(
    "D:\market-data\primary\raw\binance\futures_um\monthly",
    "D:\market-data\supplement\raw\binance\futures_um\monthly"
  ) `
  -MetricsRoot "D:\market-data\raw\binance\futures_um\daily\metrics"
```

The fixed replay interval is `[2024-01-01, 2026-08-01)` with 90 warmup days.
Accordingly, one-minute trade archives from the warmup start through July 2026
and funding/mark archives from January 2024 through July 2026 must exist for all
four symbols.  Duplicate canonical copies across roots are rejected.

For a shorter source check, call the general wrapper:

```powershell
& "$Pc\run-replay.ps1" `
  -Start "2025-02-01" `
  -End "2025-03-01" `
  -MonthlyRoot "D:\market-data\raw\binance\futures_um\monthly" `
  -MetricsRoot "D:\market-data\raw\binance\futures_um\daily\metrics" `
  -OutputName "smoke-2025-02"
```

Replay output includes `run.json`, native `fills.csv`, `positions.csv`,
`account.csv`, `trades.csv`, `episode_decisions.csv`, and the strategy SQLite
state. Existing nonempty output is never overwritten. Render actual trades and
causal no-trades with:

```powershell
$Python = ".\.venv\Scripts\python.exe"
& $Python `
  ".\research\candidate-liquidity-episode-policy-v1\production_candidate\review-replay.py" `
  --run-dir ".\artifacts\episode-policy-replay\smoke-2025-02" `
  --output ".\artifacts\episode-policy-review\smoke-2025-02"
```

Omit `-MetricsRoot` only when intentionally running without an inventory
timeline; `run.json` records that absence.  It is never silently replaced with
zero or neutral inventory.

## Testnet boundary

Funded-live execution is not implemented.  The only exchange-order mode is
Binance Futures testnet and requires both credentials and an explicit switch:

```powershell
$env:BINANCE_API_KEY = "...testnet key..."
$env:BINANCE_API_SECRET = "...testnet secret..."
& "$Pc\run-testnet.ps1" -ConfirmTestnet
```

Testnet is experimental. Exchange reports can reconcile basic identity, but
complete crash recovery of protective-order roles and emergency retries is not
part of the paper/shadow production-candidate contract. Do not infer funded-live
resumability from a successful testnet connection.
