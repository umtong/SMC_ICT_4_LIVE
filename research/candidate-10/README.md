# Candidate 10 — Causal Liquidity-Auction State Machine

Candidate 10 is an independent SMC/ICT day-trading candidate implemented as a causal market-state machine and executed exclusively by NautilusTrader. A wick, BOS, FVG, order block, session label, or fixed reward multiple is never a standalone entry signal.

## Current status

- **v0 is discarded.** Its market-after-confirmation entry and narrow event stop produced positive aggregate price PnL before commissions, but turnover and execution costs overwhelmed it. Exact evidence is in [`V0_FAILURE.md`](V0_FAILURE.md).
- **v1 is the active structural revision.** It preserves the causal raid/acceptance/rejection sequence while replacing confirmation chasing with resting structural entries, executable cost floors, and cost-adjusted structural targets.
- No success claim is made until the pinned NautilusTrader gate produces the required cost-after NAV result and the preselected follow-up weeks confirm it.

## Research hypothesis

A completed auction range creates observable external liquidity at its high and low. A boundary touch is not a direction signal. The tradable information is the subsequent auction result:

1. **Rejection** — price raids the boundary, closes back inside, and displaces through the approach structure.
2. **Acceptance** — price establishes two distinct closes outside the boundary and leaves the old range available as a retest location.

The candidate trades only after this sequence is observable at bar close. It then rests at the structural retrace rather than buying or selling the already-completed displacement.

## v1 mechanical state definitions

### Common detector

- Input: Binance USD-M `BTCUSDT` perpetual 1-minute bars.
- Bar knowledge time: `open_time + 60 seconds`; no signal uses a bar before its close.
- v1 auction range: a completed 240-minute UTC block with at least 90% of expected bars.
- v1 external pool: the previous completed block high or low.
- Raid: boundary excursion of at least `max(2 ticks, 0.08 × robust ATR60)`.
- A bar raiding both boundaries is unresolved and ignored.
- At most one setup is consumed per current auction block.

The fixed four-hour pool is an explicit v1 hypothesis, not a permanent project assumption. If v1 execution still fails, this pool generator is replaced by causally confirmed swing/equal-high/equal-low liquidity rather than optimized by hour or week.

### Rejection path

```text
POOL_ACTIVE
→ RAIDED
  boundary swept and close returned inside
→ DISPLACED
  opposite body ≥ 0.75 ATR, closes near its directional extreme,
  and breaks the last six-bar approach structure
→ ENTRY_READY
  post-only parent armed at the 61.8% displacement retrace
→ ORDER_PENDING
  expires after 16 bars or cancels on structural invalidation
→ POSITION_OPEN
→ CLOSED
```

The stop is beyond the raid extreme by the greater of:

- `1.0 × robust ATR60`; or
- one executable maker-entry/taker-stop cost floor plus two ticks.

The target is the first prior-block midpoint or opposite boundary whose **net** structural reward/risk is at least 1.35 after entry, target, stop, and tick reserves.

### Acceptance path

```text
POOL_ACTIVE
→ ACCEPTANCE_PROBE
  first close outside the boundary
→ ACCEPTED
  second distinct outside close without re-entry
→ ENTRY_READY
  post-only parent armed at the accepted boundary
→ ORDER_PENDING
  expires after 24 bars or cancels on re-entry/invalidation
→ POSITION_OPEN
→ CLOSED
```

The stop sits one executable noise/cost buffer inside the old range. The target is older block liquidity in the continuation direction; if none is available, a half-range expansion projection is eligible only when its net structural reward/risk passes the same cost-aware gate.

## Risk and Nautilus execution

- Position sizing basis: current whole-account Nautilus portfolio equity.
- Planned loss budget: `NAV × 3%`.
- Per-unit planned loss: entry-to-stop distance + maker entry fee + taker stop fee + two ticks.
- No model-score risk multiplier, candidate-specific notional cap, or strategy-level leverage cap is applied.
- Only one pending parent or position can exist.
- Entry: Nautilus post-only limit parent at the structural retrace.
- Target: contingent post-only structural limit.
- Stop: contingent stop-market.
- Pending parent: canceled on expiry, close beyond structural invalidation, funding guard, daily close, or evaluation close.
- Fill model: deterministic seed `20260806`; one-tick adverse slippage probability 1.0 for eligible aggressive fills.
- Cost-loaded metadata: maker `4 bp`, taker `7 bp`, including an ordinary fee assumption and an additional execution/adverse-selection reserve.
- Positions are flat before 00:00, 08:00, and 16:00 UTC funding windows; no funding credit is assumed.
- Within-bar high/low ordering uses NautilusTrader adaptive bar execution.

All matching, bracket contingencies, fees, positions, margin checks, reports, and NAV are produced by NautilusTrader. Candidate code does not implement a replacement backtest engine.

## Reproducible evaluation gate

The first three BTC weeks were selected before results were viewed:

```text
population: every Monday from 2022-01-03 through 2024-12-23
seed:       20260806
week 1:     2023-10-16
week 2:     2023-05-15
week 3:     2024-01-15
```

The workflow runs week 1 first. The `full` candidate and `ablation-no-acceptance` use identical data, seed, risk, costs, thresholds, and execution; the ablation changes only `enable_acceptance=False`.

A weekly gate is marked `target_pass` only when all conditions hold:

- net geometric daily NAV growth is at least 1%;
- at least seven closed, block-independent trades occur;
- at least four are winners;
- the largest winning trade contributes no more than half of gross positive PnL;
- no order denial/rejection occurs; and
- intraday NAV drawdown is below 30%.

The 1% threshold is a promotion criterion, not an optimization target or performance cap.

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

For all preselected weeks after the first gate supports continuation:

```bash
python research/candidate-10/run_research.py \
  --phase three-weeks \
  --output artifacts/candidate-10 \
  --data-root /tmp/candidate-10-data
```

Every Binance archive is verified against its published checksum. Each isolated Nautilus process writes:

- data and run manifests;
- metrics and daily NAV;
- Nautilus order, position, and account reports;
- causal scenario event log;
- enriched trade ledger with holding time, MFE, MAE, actual entry/exit, commissions, and exit class;
- equity curve and order-error ledger; and
- turnover, maker/taker fill, gross-price-PnL, and commission diagnostics.

## Error classification policy

- Environment, API, timestamp, data, order, callback, accounting, or artifact failure is an implementation error. It is corrected under variable control and the same week is rerun.
- A completed run with insufficient opportunity or weak cost-after expectancy is a logic result.
- The required one-variable acceptance ablation is performed once under identical execution conditions.
- A failed version is not rescued by a parameter grid. A structural causal improvement is stated first, implemented once, and tested on the same week.
- A valid component inside a failed candidate is recorded separately rather than misreported as candidate success.

## Known failure conditions

- A fixed four-hour boundary may not contain meaningful resting liquidity; repeated failure after v1 invalidates the pool generator.
- A raid can reverse without certified displacement, producing no trade.
- Price can continue without retracing to the resting parent, producing a correct read but no fill.
- A post-only parent can be rejected if a gap makes it marketable; that is recorded as an order error, not silently converted to a market fill.
- A fast retest entirely inside the confirmation bar is unobservable at 1-minute resolution and is deliberately not inferred.
- A cost-qualified structural target may not exist, invalidating an otherwise directionally plausible setup.
- Bar data cannot reproduce queue position, intrabar depth depletion, liquidation cascades, or nonlinear market impact. The fee reserve and deterministic slippage remain approximations requiring later tick/order-book validation.
- Constant margin metadata approximates a live tiered exchange schedule. Any promoted candidate still requires live tier, liquidation-distance, and venue-rule validation.
