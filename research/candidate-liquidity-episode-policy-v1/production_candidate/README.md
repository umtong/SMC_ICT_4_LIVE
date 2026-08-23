# Liquidity Episode Policy — integrated production candidate

This directory packages the integrated research policy under
`src/smc_ict_4/episode_policy_live`.  The same NautilusTrader strategy is used
for historical replay and connected public-market operation.  It is a
paper/shadow production candidate, not a funded-live release and not a claim of
future profitability.

The branch synthesis and the earlier implementations reused here are recorded
in [BRANCH_AUDIT.md](BRANCH_AUDIT.md) and
[RESEARCH_SYNTHESIS_PROVENANCE.md](RESEARCH_SYNTHESIS_PROVENANCE.md).  Those
mechanisms are not presented as newly discovered Missing Pieces.

## Fixed trading contract

- BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT share one margin account and one global
  pending/position slot.
- Entry, structural stop and liquidity target are fixed before submission; no
  scaling in or out is used.
- A plan is not emitted below 1.0 gross R.
- Quantity is derived from current mark-to-market NAV so the rounded planned
  entry-to-structural-stop price loss is approximately 3% of NAV. Entry/exit
  fees, adverse stop slippage and gaps are recorded economic losses on top;
  they do not reduce the structural-risk quantity.
- Native Nautilus orders, fills, portfolio, margin accounting and reports are
  used.  Historical funding is applied to the native account from official
  funding-rate and mark-price archives.

Passing a software contract job proves only that these invariants and the
runtime integration execute.  Performance evidence requires a source-bound
`run.json`, native ledgers, and a separately recorded exact code SHA.

## CLI contract

After installation, `lep --help` exposes:

```text
lep verify [--build-node] [--state PATH] [--output PATH]
lep replay --start YYYY-MM-DD --end YYYY-MM-DD \
  --monthly-root PATH [--monthly-root PATH ...] [--metrics-root PATH] --output PATH
lep run --mode {shadow,sandbox,testnet} --state PATH [--duration-seconds N]
lep status --state PATH [--mode {shadow,sandbox,testnet}]
lep backup --state PATH --output PATH
```

`lep replay` never downloads or silently repairs data.  Every `--monthly-root`
must be an existing Binance Vision `futures_um/monthly` directory.  Before the
engine starts, the source resolver requires exactly one canonical file for each
symbol/month and each of these trees:

```text
klines/<SYMBOL>/1m/<SYMBOL>-1m-YYYY-MM.zip
fundingRate/<SYMBOL>/<SYMBOL>-fundingRate-YYYY-MM.zip
markPriceKlines/<SYMBOL>/1m/<SYMBOL>-1m-YYYY-MM.zip
```

Multiple roots are useful when later months live in a supplemental archive.
Two roots containing the same canonical symbol/month are rejected as ambiguous.
An output directory must be absent or empty; replay refuses to overwrite
evidence.

`--metrics-root` is optional.  When supplied, it must be the canonical Binance
Vision USD-M `daily/metrics` root with one archive and official checksum per
symbol/day:

```text
<SYMBOL>/<SYMBOL>-metrics-YYYY-MM-DD.zip
<SYMBOL>/<SYMBOL>-metrics-YYYY-MM-DD.zip.CHECKSUM
```

The replay requires the preceding UTC day for causal inventory warmup.  Missing,
duplicate or checksum-invalid metrics fail before trading; omitting the option
is recorded explicitly as no inventory timeline rather than neutral inventory.

## Connected modes

| Mode | Data | Execution | Intended use |
|---|---|---|---|
| `shadow` | live Binance USD-M public trades and mark/funding updates | Nautilus in-process sandbox account | bounded signal/order observation without exchange orders |
| `sandbox` | same public feed | Nautilus in-process sandbox account | explicit paper order-lifecycle exercise |
| `testnet` | Binance USD-M testnet | Binance testnet adapter | experimental acknowledgement testing only |

`shadow` and `sandbox` do not submit exchange orders and currently share the
same in-process sandbox execution adapter.  The labels express operating
intent, not two independent accounting engines.  Testnet requires explicit
testnet credentials and `--confirm-testnet`; funded-live execution is absent.

The SQLite store uses WAL, full synchronization, atomic snapshots and a chained
event log for policy/market/runtime evidence.  However, the in-process sandbox
client itself has no durable external account backing.  After any sandbox order
is accepted or filled, that state file is deliberately non-restartable even if
the account later appears flat: the process-local balance and matching history
cannot be reconstructed.  `lep status --mode ...` reports the executable restart
boundary.  Archive the old state and choose a new state path instead of claiming
resume.  Testnet exchange reports are available, but complete crash recovery of
protective-order roles and emergency retries is not yet a funded-live contract;
testnet must not be presented as proof of live-capital resumability.

The connected default downloads exactly 10,080 contiguous completed public
one-minute bars per symbol (seven days) before startup. It paginates the public
Binance endpoint and aborts before inserting any new symbol window if one of
the four cannot be obtained completely. `lep run --bootstrap-minutes N` and
`lep bootstrap --limit N` permit an explicitly shorter diagnostic window, but
that is not described as policy-ready warm-up. Connected SHADOW, SANDBOX and
TESTNET also seed and poll public five-minute OI/global-account-ratio metrics;
failed, stale or timestamp-unjoined polls clear the policy timeline to UNKNOWN
instead of reusing cached inventory. A short connection check still does not
guarantee that a trade will appear.

## Trade and no-trade review

Native replay writes `episode_decisions.csv` beside `trades.csv`.  It contains
one causal START and at most one terminal SELECTED/NO_TRADE result per episode;
an incomplete episode remains explicit rather than being converted into a
loss or a missed win.  Future price outcomes are not policy evidence.

Parent-order evidence separates `planned_entry_price` from an immediate IOC's
`execution_limit_price`. `planned_structural_stop_loss` is the requested
quantity's entry-to-stop price risk; `estimated_all_in_stop_loss` separately
shows adverse-stop price loss and fees. If an entry is only partially filled,
the trade ledger preserves the full parent intent but scales `risk_cash` and
`actual_filled_structural_risk_fraction` by the observed fill fraction.

Render every actual trade plus a deterministic reason/family/symbol sample of
terminal no-trades with the reused branch chart clinic:

```bash
python research/candidate-liquidity-episode-policy-v1/production_candidate/review-replay.py \
  --run-dir artifacts/episode-policy-replay/continuous \
  --output artifacts/episode-policy-review/continuous
```

The review output binds its input files and official archives by hash.  Any
later target-first/stop-first label is marked `OFFLINE_AUDIT_ONLY` and never
feeds the executable policy.

## Docker

Build from the repository root:

```bash
docker build \
  -f research/candidate-liquidity-episode-policy-v1/production_candidate/Dockerfile \
  -t smc-ict-liquidity-episode:local .
docker run --rm -v lep-state:/var/lib/lep smc-ict-liquidity-episode:local
```

The default container command runs `lep verify --build-node`.  Connected shadow
operation can be started from the repository root with:

```bash
docker compose \
  -f research/candidate-liquidity-episode-policy-v1/production_candidate/compose.yaml \
  up --build shadow
```

Use `docker compose ... --profile sandbox up --build sandbox` to start only the
sandbox service.  Both services need outbound access to Binance public APIs.
Their restart policy is deliberately `no`; automatically restarting a mutated
process-local sandbox state would only hit the fail-closed restart boundary.

## GitHub Actions contract

Pushes run Linux, Windows and Docker contracts.  Linux and Windows run the full
`pytest tests` suite, then build a Nautilus node and verify the durable store.
The Docker job builds the shipped image and performs the same node-build check.

Connected public-market and long continuous jobs are manual opt-ins.  The
repository does not contain 2.5 years of market data, so the long job cannot
truthfully pass from source checkout alone.  A dispatch must select exactly one:

- `monthly_data_cache_key`: an existing Actions cache whose
  `data/binance-monthly` directory is one canonical monthly tree; or
- `monthly_data_artifact` plus `monthly_data_run_id`: an artifact from that
  repository/run containing that same tree at its root.

Daily inventory metrics are independently optional.  Select at most one
`metrics_data_cache_key`, or `metrics_data_artifact` plus
`metrics_data_run_id`.  Its restored `data/binance-metrics` directory must have
the canonical symbol/archive/checksum layout shown above.  When neither is
selected, the replay records `NO_INVENTORY_TIMELINE`.

The job fails on a cache miss, missing input, duplicate source selection, or a
missing canonical archive.  CI smoke/contract success is never recorded as a
long-replay or strategy-performance result.

Python 3.13 and NautilusTrader 1.230.0 are direct project pins.  The Docker image
uses the committed `uv.lock` for transitive dependencies.  The Windows helper
uses the pinned direct Nautilus requirement but installs through pip, so it does
not claim a hash-locked transitive environment.

Windows 11 commands are in [README_WINDOWS11.md](README_WINDOWS11.md).
