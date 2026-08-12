# candidate-easychart_v4

This candidate rebuilds the EasyChart decision layer while retaining the
already-audited NautilusTrader account, bracket, fee, slippage, funding, sizing,
and four-symbol global-slot implementation from v3.

## Why v4 exists

The latest v3 diagnostic completed 20 trades over 14 calendar days with no
risk-budget breach or protective-order failure, but NAV fell from 100,000 to
83,487.19. Execution validity passed; the market interpretation did not.
The main error was treating OB/FVG overlap itself as the complete context and
using small footprint edges as acceptance boundaries and stop geometry.

v4 therefore makes the source hierarchy explicit:

```text
confirmed wick structure / liquidity
    -> boundary interaction
       BOUNCE | FAKEOUT | TRAP_REENTRY | ACCEPTANCE
    -> event-local OB or FVG displacement
    -> first later retest with closed-bar reaction
    -> one predeclared structural stop
    -> one predeclared structural target
```

OB and FVG remain important, but they refine a meaningful structural event;
they are not standalone buttons. Trendlines and channels supply direction,
boundaries, liquidity and objectives. Fakeout/Trap and accepted breakout are
opposite transitions of the same boundary state.

## Fixed account and execution contract

- Universe: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT.
- Across all four symbols: at most one pending entry or open position.
- One entry, one full stop, one full target; no partial management.
- Entry, stop and target are fixed before submission.
- Gross pre-cost RR must be at least 1.0 after the structural target is chosen.
- Planned worst loss is current account NAV × 3%, including estimated entry and
  stop fees, slippage reserve and funding.
- No daily loss limit, trade-count cap, nominal cap, leverage cap, score risk
  multiplier or discretionary size reduction.
- Orders, matching, positions, account and reports are NautilusTrader objects.

## Decision objects

`market_structure.py` is closed-bar and causal. It confirms wick pivots only
after the right-hand bars close, joins consecutive directional pivots, creates
an exactly parallel channel only after an opposite third point, and permits a
channel trade only at a later fourth interaction. It invents no angle gate.

A wick outside with a close inside is `FAKEOUT`. A body close outside is only a
break attempt. `ACCEPTANCE` requires the next context candle to open and close
outside; a return inside is `TRAP_REENTRY`. A bounce which cannot reach the
channel midline before accepted failure is tagged
`CHANNEL_FAILURE_ACCEPTANCE`.

`scenario_bundle_v4.py` applies that same grammar at 60m→5m and 15m→1m. The
scales are not separate bots. They produce immutable plans for the same global
router and continuous account.

## Diagnostic run

```bash
python research/candidate-easychart_v4/run_v4_backtest.py \
  --start 2024-02-01 --end 2024-02-14 --warmup-days 30 \
  --symbols BTCUSDT ETHUSDT SOLUSDT XRPUSDT \
  --cache .cache/candidate-easychart_v4 \
  --output artifacts/candidate-easychart_v4/mtf
```

The short period is development data. Its purpose is to inspect expected vs
actual structure, events, plans, fills and costs trade by trade before deciding
whether a larger evaluation has information value.
