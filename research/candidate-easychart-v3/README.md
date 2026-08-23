# candidate-easychart-v3

`candidate-easychart_v3` is an independent EasyChart automation candidate. It reuses NautilusTrader execution/accounting from v2, but replaces the weak pattern-first decision policy with a causal market-scenario policy.

```text
60m context zone + 15m decision zone
→ actual shared price area
→ interaction with the area's liquidity
→ REJECTION / ACCEPTANCE / UNRESOLVED
→ event-local 5m execution structure or S/R flip
→ first retest only
→ predeclared entry / causal stop / pre-existing opposite target
```

## Fixed execution contract

- Universe: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT`.
- Across all four instruments, at most one pending new entry or open position.
- Planned loss budget is current continuous-account NAV × 3%.
- Entry, stop and target are fixed before order submission.
- A trade requires pre-cost planned reward/risk of at least `1.0R`.
- One full entry, one full stop, one full target. No strategic scale-in, partial take-profit, partial stop, breakeven move or trailing stop.
- No daily loss limit, losing-streak pause, trade-count cap or arbitrary cooldown.
- NautilusTrader owns orders, fills, fees, positions and NAV. There is no private matching/account engine.

## v3 decision families

### 1. `SWEEP_RECLAIM_DISPLACEMENT_RETEST`

A context-area excursion is not a trade. Price must reclaim the entire shared context, then form a later same-direction 5m OB/FVG whose formation originates at the context. Entry is permitted only on that structure's first later retest and reaction. Invalidation is beyond the causal sweep extreme, not behind a conveniently narrow trigger candle.

### 2. `ACCEPTANCE_HOLD_FLIP_RETEST`

A 15m close outside the context is only a break candidate. The next 15m candle must open and close outside. Entry is permitted only when a later 5m bar retests the breached context from the outside and keeps it as flipped support/resistance.

### 3. `UNRESOLVED`

A partial reclaim, failed first retest, failed next-bar hold, missing pre-existing objective, or sub-1R geometry is recorded and not silently converted into another setup after seeing the outcome.

## Why this is not v2 plus more filters

The v2 short diagnostic produced many generic pivot rejections with attractive planned R multiples but repeated near-immediate stop-outs. v3 removes that causal error. OB/FVG is no longer a global standalone signal, a sweep is no longer sufficient by itself, and a lower-timeframe trigger cannot use its own formation candle as a retest.

## Run

```bash
smc4 doctor
PYTHONPATH=research/candidate-easychart-v3 \
python research/candidate-easychart-v3/run_mtf_backtest.py \
  --start 2024-02-01 --end 2024-02-14 --warmup-days 30 \
  --symbols BTCUSDT ETHUSDT SOLUSDT XRPUSDT \
  --cache .cache/candidate-easychart-v3 \
  --output artifacts/candidate-easychart-v3/mtf
```

`decision_events.csv`, `scenario_events.jsonl`, `mtf_trade_windows.jsonl` and `trade_audit.csv` preserve both selected trades and the state transitions which rejected nearby lookalikes.
