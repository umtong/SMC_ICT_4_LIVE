# Candidate 01 — Causal Liquidity Auction

Candidate 01 is one complete, falsifiable SMC/ICT-style day-trading hypothesis. It does not trade isolated candle labels. It models an auction around previously completed external liquidity, classifies the market response as **rejection** or **acceptance**, waits for a causally observable confirmation, and only then creates a risk-defined trade plan.

The same `AuctionStateMachine` is independent of NautilusTrader. NautilusTrader owns replay, orders, contingent protection, fills, fees, margin, liquidation, positions, and account NAV.

## Hypothesis

A completed four-hour UTC range creates two visible pools of external liquidity. The first material traversal of a boundary is not itself a signal. It starts one of two mutually exclusive causal paths:

```text
completed range
    ├── boundary traversed, price closes back inside
    │       └── opposing displacement breaks prior internal structure
    │               └── causal imbalance/body retrace is rejected
    │                       └── trade toward the opposing range liquidity
    │
    └── boundary traversed, price closes and aggressive flow remain outside
            └── second directional outside close confirms acceptance
                    └── boundary retest holds
                            └── trade toward a measured range projection
```

The economic claim is modest and testable: clustered stop orders and breakout orders make completed range boundaries consequential; signed aggressive flow and subsequent price acceptance distinguish a failed auction from value migration better than a wick label alone. This claim is allowed to fail.

## Programmed definitions

| Concept | Machine definition |
|---|---|
| External liquidity | High/low of the immediately preceding, sufficiently complete, fixed four-hour UTC range |
| Sweep | Current high/low crosses a range boundary by a prior-ATR-scaled minimum |
| Rejection | Sweep closes back inside the completed range without an excessive excursion |
| Acceptance | Close remains outside by an ATR-scaled distance with same-direction aggressive-flow z-score |
| Internal structure | Highest/lowest completed bar over the preceding structural lookback; never a future-confirmed pivot |
| Displacement | Opposing body closes through internal structure with ATR-scaled body and flow confirmation |
| FVG | Three-bar imbalance observable only after the third bar closes; otherwise a displacement-body retrace zone |
| Premium/discount | Entry and target are evaluated relative to the completed range and the invalidation extreme, not a hindsight swing |
| Invalidation | Sweep extreme or failed accepted-boundary retest, with an ATR buffer |
| Time invalidation | Every phase has a finite confirmation/retrace window; positions have a finite maximum holding time |

Every detector consumes only completed one-minute bars. Each scenario event records both event time and observation time. The implementation rejects non-monotonic timestamps.

## Execution and account risk

The signal generated at bar `N` cannot be filled on that same observation. The adapter waits until bar `N+1`, rechecks price ordering and net reward/risk after costs, and submits a market-entry bracket through NautilusTrader. The protective exit is a stop-market order; the target is contingent. Bar replay uses adaptive high/low ordering, stop orders are enabled explicitly, and engine liquidation is enabled.

Quantity is derived only from current account equity and expected loss:

```text
risk_budget = current_NAV × risk_fraction
per_unit_loss = |delayed_entry - stop| + entry_cost + stop_cost
quantity = floor_to_venue_increment(risk_budget / per_unit_loss)
```

There is no strategy-level notional cap, score multiplier, volatility multiplier, or discretionary leverage reduction. The venue margin model can reject an unaffordable order rather than silently resizing it. The run records effective leverage and the minimum equity-to-maintenance-margin ratio.

The default cost stress is **7 bps per side**, charged by the engine on every fill and included in risk sizing. It is an explicit composite allowance for taker fee, spread/slippage, market impact, and possible funding over a maximum two-hour hold; it is not presented as a universal Binance fee quote.

## Reproducible research protocol

The random-week order was frozen before observing candidate results:

```bash
python research/candidate-01/seed_protocol.py
```

Seed `4012026`, Monday pool `2022-01-03` through `2025-12-22`:

1. discovery: `2023-06-19`
2. confirmation 1: `2022-08-01`
3. confirmation 2: `2025-11-10`
4. additional 1: `2025-12-15`
5. additional 2: `2023-02-13`
6. held back by the current protocol: `2022-11-21`

The workflow enforces the sequence rather than exposing all held-out weeks on the first run. A normal branch push runs only the discovery week. The two untouched confirmation weeks run only through a manual `quick` dispatch or a commit deliberately marked `[quick]`. The fixed 2024 calendar year is not evaluated until a deliberate `full` run. BTCUSDT is the first experimental venue, as required by the project protocol. ETHUSDT, SOLUSDT, and XRPUSDT are not used for parameter optimization.

Run inside the prebuilt project environment:

```bash
smc4 doctor
python -m unittest discover -s tests -p 'test_*.py' -v
python research/candidate-01/run_research.py --suite discovery
# Only after the discovery result has been judged:
python research/candidate-01/run_research.py --suite quick
python research/candidate-01/run_research.py --suite extended
python research/candidate-01/run_research.py --suite full
```

Raw Binance Vision archives are cached outside Git. Each run records URL, byte length, local SHA-256, and the publisher checksum when available.

## Declared completion gate

`candidate_success` is true only when all of the following hold without changing the gate after results are observed:

- each seeded random week reaches at least 1% cost-after geometric mean daily NAV growth and closes at least five positions;
- the full 2024 evaluation reaches at least 1% cost-after geometric mean daily NAV growth and closes at least 100 positions;
- pooled geometric daily growth is at least 1%;
- worst segment maximum drawdown is less than 20%;
- every submitted entry becomes one closed position, every run ends flat, and the global entry gate is never violated;
- engine liquidation is enabled, no report contains a liquidation marker, no protective-order failure occurs, and equity remains above maintenance margin in sampled NAV states.

A failed gate is a rejected candidate, not partial success.

## Evidence layout

Each segment writes the repository output contract under `artifacts/candidate-01/<suite>/<segment>/`:

```text
run.json
metrics.json
data_manifest.json
scenario_events.jsonl
execution_events.jsonl
orders.csv
positions.csv
account.csv
trade_plans.csv
daily_nav.csv
```

The suite root adds `aggregate_metrics.json` and `run.json`. GitHub Actions preserves the entire directory even when the full completion gate fails.

## Files

- `core.py` — event detectors and causal scenario state machine only
- `data.py` — deterministic public-data acquisition and integrity checks
- `nautilus_backtest.py` — Nautilus order/account/margin adapter and evidence writer
- `run_research.py` — frozen evaluation protocol and completion gate
- `seed_protocol.py` — independent reproduction of random-week ordering
- `config.json` — one structural parameter set and execution assumptions
- `RESEARCH.md` — external evidence, adjacent-domain review, and adopted/rejected ideas
- `FAILURE_CONDITIONS.md` — explicit falsification and live invalidation conditions
- `LIVE_PARITY.md` — exact path from the tested state machine to four-instrument live operation
- `RESULTS.md` — immutable recorded run summary once the workflow completes
