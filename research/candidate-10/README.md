# Candidate 10 — Causal Liquidity-Auction State Machine

Candidate 10 is an independent SMC/ICT day-trading candidate implemented as a causal market-state machine and executed exclusively by NautilusTrader. It does not treat a wick, BOS, FVG, order block, or session label as a standalone entry signal.

## Research hypothesis

A completed four-hour UTC auction block provides observable external liquidity at its high and low. When price reaches one of those boundaries, the next tradeable information is not the raid itself but the auction result:

1. **Rejection** — price raids the boundary, closes back inside, displaces through the approach structure, and rejects the first retrace into the displacement corridor.
2. **Acceptance** — price closes beyond the boundary twice, then retests and holds the accepted boundary before continuing toward older external liquidity or a range expansion projection.

A trade is submitted only after the complete causal sequence is observable at bar close. Targets are structural liquidity locations, not fixed arbitrary R multiples.

## Mechanical state definitions

### Common detector

- Input: Binance USD-M `BTCUSDT` perpetual 1-minute bars.
- Bar knowledge time: `open_time + 60 seconds`; no signal uses the bar before its close.
- Auction range: a completed 240-minute UTC block with at least 90% of expected bars.
- External liquidity pool: prior completed block high or low.
- Raid: boundary excursion of at least `max(2 ticks, 0.08 × robust ATR60)`.
- A bar raiding both boundaries is unresolved and ignored.
- At most one setup and one trade are consumed per current auction block.

### Rejection path

```text
POOL_ACTIVE
→ RAIDED (boundary swept and close returned inside)
→ DISPLACED (opposite body ≥ 0.75 ATR and breaks the last six-bar approach structure)
→ ENTRY_READY (first 38.2–61.8% corridor retrace rejects in the new direction)
→ ORDER_PENDING
→ CLOSED
```

The stop is beyond the raid extreme plus a `0.12 ATR` buffer. The target is the prior block midpoint or opposite boundary, whichever is the first structural target with sufficient room.

### Acceptance path

```text
POOL_ACTIVE
→ ACCEPTANCE_PROBE (close outside the boundary)
→ ACCEPTED (second outside close)
→ ENTRY_READY (first boundary retest holds and closes in the accepted direction)
→ ORDER_PENDING
→ CLOSED
```

The stop is beyond the retest/boundary invalidation. The target is older block liquidity in the continuation direction; if none exists, a half-range expansion projection is used.

### Expiry and invalidation

A setup expires when its required next state does not occur within the declared bar budget. It is invalidated when price establishes the opposite auction result, breaks the raid extreme before entry, re-enters an accepted range, or lacks a structural target with sufficient room. Block rollover is explicitly logged as expiry rather than silently resetting state.

## Risk and execution

- Position sizing basis: current whole-account Nautilus portfolio equity.
- Planned loss budget: `NAV × 3%`.
- Per-unit planned loss includes entry-to-stop distance, aggressive entry and stop fees, and a two-tick reserve.
- No model-score risk multiplier, candidate-specific notional cap, or strategy-level leverage cap is applied.
- Only one pending entry/position can exist because the strategy is single-instrument and refuses a new order unless the portfolio is flat and no entry is pending.
- Entry: Nautilus market parent.
- Exit: contingent target limit plus stop-market bracket.
- Target post-only is disabled so a crossed target cannot leave an unprotected/rejected child.
- Fill model: one-tick adverse slippage with deterministic seed `20260806`.
- Cost-loaded metadata: maker `4 bp`, taker `7 bp`. These include the ordinary venue fee assumption plus an additional execution/adverse-selection reserve.
- Positions are flattened before the 00:00, 08:00, and 16:00 UTC funding windows and before daily/evaluation close; therefore no funding credit is assumed.
- Within-bar high/low ordering uses NautilusTrader adaptive bar execution.

All order matching, bracket contingencies, fees, position accounting, margin checks, reports, and NAV are produced by NautilusTrader. Candidate code does not implement a replacement backtest engine.

## Reproducible evaluation gate

The first three BTC weeks are selected before results are viewed:

```text
population: every Monday from 2022-01-03 through 2024-12-23
seed:       20260806
week 1:     2023-10-16
week 2:     2023-05-15
week 3:     2024-01-15
```

The gate workflow runs week 1 first. The `full` candidate and one-variable `ablation-no-acceptance` variant use identical thresholds, risk, data, and execution assumptions. The ablation removes only the acceptance path.

A weekly gate is marked `target_pass` only when all of the following hold:

- net geometric daily NAV growth is at least 1%;
- at least seven closed, block-independent trades occur;
- at least four are winners;
- the largest winning trade contributes no more than half of gross positive PnL;
- no order denial/rejection occurs; and
- intraday NAV drawdown is below 30%.

This gate is not an optimization objective and does not cap growth above 1%. It is an efficient screening rule before the additional random weeks and longer evaluation.

## Reproduction

In the prebuilt project environment:

```bash
smc4 doctor
python -m unittest discover -s research/candidate-10 -p 'test_*.py' -v
python research/candidate-10/run_research.py \
  --phase gate \
  --output artifacts/candidate-10 \
  --data-root /tmp/candidate-10-data
```

For all preselected weeks:

```bash
python research/candidate-10/run_research.py \
  --phase three-weeks \
  --output artifacts/candidate-10 \
  --data-root /tmp/candidate-10-data
```

Every Binance archive is paired with and verified against its published checksum. Each run writes a data manifest, run manifest, metrics, Nautilus order/position/account reports, scenario event log, trade ledger, equity curve, and order-error ledger.

## Error classification policy

- Environment, API, timestamp, event-chain, data, order, and accounting failures are implementation errors. They are corrected under variable control and the same week is rerun.
- A completed run with insufficient opportunity or weak cost-after expectancy is a logic result. The required acceptance-path ablation is compared once; if the failure has no structural improvement path, the candidate is discarded rather than parameter-mined.
- A profitable component inside an overall failed candidate is recorded separately, including the market state in which it worked and why it did not generalize.

## Known failure conditions declared before results

- A four-hour block boundary may be economically irrelevant during continuous one-direction repricing; the acceptance path is intended to capture rather than fade that case, but can still enter late.
- A raid can reverse without a body large enough to satisfy displacement, producing no trade.
- A displacement corridor can be skipped without retrace, producing no trade.
- A fast retest can occur inside the same 1-minute bar as confirmation and remain unobservable at bar resolution; the candidate deliberately does not infer that fill.
- A structural target can be too close after costs, invalidating an otherwise correct directional read.
- Bar data cannot reproduce queue position, intrabar depth depletion, liquidation cascades, or nonlinear market impact. The fee reserve and deterministic one-tick slippage are conservative approximations, not substitutes for later tick/order-book validation.
- Constant instrument margin metadata approximates a tiered live exchange margin schedule. A successful candidate still requires live-venue tier and liquidation-distance validation before deployment.
- Fixed UTC four-hour blocks may underperform when liquidity migrates to different activity windows; this is a logic failure condition, not a reason to optimize separate hours per week.
