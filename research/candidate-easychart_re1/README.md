# candidate-easychart_re1

`candidate-easychart_re1` is the first EasyChart candidate in this lineage which gives the recorded 60-minute chart an explicit decision role instead of treating it as audit-only data.

## Trading policy

The executable scenario remains the v20 diagonal core:

1. A causal 15-minute wick trend line or parallel channel already exists.
2. A 5-minute interaction is classified as rejection/re-entry, rotation/bounce, or accepted break.
3. Where the path requires a footprint, a newly formed 1-minute OB/FVG confirms displacement.
4. Price must close away and then make a distinct first return; the completed retest fixes entry and structural stop.
5. A pre-existing opposing structure or the first channel extension fixes the target before submission.
6. Gross planned RR must be at least 1.0.

RE1 adds top-down routing:

- A close-confirmed break of a previously confirmed 60-minute wick swing establishes the current structural side.
- Local plans in that direction are continuation opportunities.
- Before any such break has established a side, the market is treated as range/transition rather than forced long or short.
- A local plan against the current 60-minute side is allowed only when its decision area intersects a pre-existing same-side 60-minute structure, OB, or FVG. The lower-timeframe reclaim/retest is still required; an HTF area is never an unconditional entry.

This is a deterministic translation of the supplied top-down trading cases, not a claim that the source stated a numeric BOS algorithm.

## Fixed account contract

- BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT
- one continuous NautilusTrader account and one global pending order/position slot
- one entry, one full stop, one full target
- risk budget: 3% of current total NAV, including the configured cost/slippage reserve
- no daily trade cap, no daily loss cap, no partial management, no break-even move
- no order when the planned price is not reached

## Diagnostic run

```bash
PYTHONPATH=research/candidate-easychart_re1:research/candidate-easychart-v5:research/candidate-easychart-v3 \
python research/candidate-easychart_re1/run_mtf_backtest_re1.py \
  --start 2025-02-01 \
  --end 2025-02-14 \
  --warmup-days 30 \
  --symbols BTCUSDT ETHUSDT SOLUSDT XRPUSDT \
  --cache .cache/candidate-easychart-re1/february-2025 \
  --output artifacts/candidate-easychart-re1/february-2025 \
  --fee-profile usd_m_vip0
```

The event stream records every HTF direction change, continuation approval, HTF reversal-area exception, and rejection against the HTF side. The same strategy and account code is intended for longer backtests and paper/live adapters; only the runner configuration changes.
