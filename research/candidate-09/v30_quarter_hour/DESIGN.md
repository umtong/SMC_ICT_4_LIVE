# Candidate 09 v30 — quarter-hour algorithmic-flow delivery

This candidate is frozen before its first economic run. It does not reuse the contaminated three-week screen.

## External mechanism

Kim and Hansen (2026) document that Binance perpetual-futures activity bursts at quarter-hour openings, that opening returns are predictable from boundary-aligned lags, and that opening order imbalance predicts four-to-twelve-hour returns. The market mechanism is scheduled algorithmic execution whose directional flow persists across clock boundaries.

## Complete scenario

```text
true quarter-hour opening
→ prior completed quarter-hour openings show persistent direction
→ current completed opening minute moves in that direction
→ current taker imbalance agrees
→ enter after the opening minute
→ invalidate beyond that same opening impulse
→ target nearest still-unconsumed completed four-hour auction extreme
→ flatten by twelve hours or before UTC midnight
```

The opening signal, invalidation and target all belong to the new delivery leg. If the natural objective does not leave at least 1.2 net R after full composite costs, the state is `NO_TRADE`.

## Frozen evaluation

1. One continuous BTCUSDT account during 2023 development.
2. Unchanged 2024 validation only after development passes.
3. Unchanged 2025 final evaluation only after 2024 passes.

## Single-variable controls

- `shifted-phase`: identical rule at minute phases 7/22/37/52 instead of the true quarter-hour grid.
- `no-imbalance`: remove only current taker-imbalance agreement.
- `no-boundary-lag`: remove only prior boundary-opening persistence.

No parameter search, no repeated ablation promotion, and no score-based risk multiplier are permitted.
