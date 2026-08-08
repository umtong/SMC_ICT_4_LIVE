# Candidate 37 — Causal Large-Auction-Event Router

Candidate 37 reuses the verified Candidate 29/35 input ownership, four-symbol synchronization, NautilusTrader execution, one global entry slot, realistic cost reservation and exactly 3% current-NAV planned-loss sizing. It does not reuse their alpha.

## v1: burst-shape router — rejected before execution

The first version separated synchronous common-shock laggard propagation and isolated endogenous-ramp failure. The causal 2026-07-01 through 2026-07-07 study selected 147 independent routes, but only 38 reached the declared objective before the stop. Synchronous propagation produced 20 target-first outcomes from 109 routes; endogenous exhaustion produced 18 from 38. Median price risk was about 8 bp while reserved round-trip cost was 21 bp, so the typical objective itself was smaller than cost. Median 30-minute markout was negative.

The failure is structural rather than a threshold problem: predicting another 10–20 bp after a burst cannot support the project under the reserved execution costs. v1 is rejected and was not promoted to NautilusTrader PnL/NAV evaluation.

## v2: large-auction-event policy

v2 changes the unit of trade. A route is not created unless the pre-declared structural objective is at least 65 bp and next-open price risk remains between 10 and 90 bp. It separates three causal mechanisms:

1. **`RISK_BUILD_BREAKOUT`** — a compressed 60-minute auction breaks with new open interest, aligned aggressor flow, efficient displacement and a confirming peer. The target is the measured range projection; invalidation is material acceptance back inside the old range.
2. **`DELEVERAGING_CONTINUATION`** — a common large move occurs while open interest contracts, remains accepted through a pause and re-accelerates. The target extends the forced-deleveraging leg.
3. **`FAILED_CASCADE_REVERSAL`** — a common deleveraging extreme fails, opposite aggressor flow appears and price reclaims toward the pre-event anchor. The extreme invalidates the route.

Everything else is `UNRESOLVED / NO TRADE`. Score only arbitrates simultaneous valid routes; it never changes the 3% risk budget.

## Causal and claim contracts

- Decisions use completed one-minute observations only.
- Price, trade-flow, OI/positioning and premium observations retain their source ownership and publication delays.
- ATR, ranges and baselines exclude the candidate bar.
- All four symbols share one exact minute clock.
- Entry geometry is rechecked at the next bar open; gaps that destroy risk/objective geometry are rejected.
- Repeated observations of one causal event are collapsed and a global 120-minute event lockout is applied.
- Future bars only label target/stop/timeout after a route is fixed. Structural studies do not simulate orders, fills, positions, PnL or NAV.
- Only stable positive development and validation evidence may proceed to untouched NautilusTrader execution.

## Efficient validation order

The first long-span screen is `.github/workflows/candidate-37-v2-lightweight-study.yml`. It pins Candidate 30's already-tested month builder by commit SHA and uses completed-minute price/taker-volume, delayed OI/positioning and premium observations for all four symbols. The unchanged policy is applied to 2025-09 development and 2025-10 validation. Minute taker-buy quote share is explicitly a coarse flow proxy; the screen can reject a weak price/OI mechanism but cannot replace rich aggTrade/depth confirmation.

The expensive `.github/workflows/candidate-37-v2-large-event-study.yml` uses Candidate 29 rich aggTrade/depth features and is intentionally run only after the lightweight mechanism screen warrants that cost.

Both studies write a JSON verdict, route-level CSV and console log. Neither makes a PnL or NAV claim.

## Reproduction

```bash
smc4 doctor
python -m unittest research/candidate-37/test_large_event_router.py -v
python research/candidate-37/v2_lightweight_study.py \
  --input-root artifacts/c37-light-downloaded \
  --output artifacts/c37-light-study \
  --start 2025-09-01 \
  --end 2025-10-31 \
  --split 2025-10-01 \
  --horizon-minutes 180 \
  --lockout-minutes 120
```
