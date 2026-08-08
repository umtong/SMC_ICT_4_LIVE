# Candidate 37 — Causal Large-Auction-Event Router

Candidate 37 reuses the verified Candidate 29/35 input ownership, four-symbol synchronization, NautilusTrader execution, one global entry slot, realistic cost reservation and exactly 3% current-NAV planned-loss sizing. It does not reuse their alpha.

## v1: burst-shape router — rejected before execution

The first version separated:

- synchronous common shocks followed by laggard propagation;
- isolated endogenous activity ramps followed by failed-extreme reversal.

The causal seven-day study used BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT from 2026-07-01 through 2026-07-07. It selected 147 independent routes, but only 38 reached the declared objective before the stop. Synchronous propagation produced 20 target-first outcomes from 109 routes; endogenous exhaustion produced 18 from 38. More importantly, the median price stop was about 8 bp while the reserved round-trip cost was 21 bp, so the median target itself was smaller than cost. Median 30-minute markout was negative. The version is therefore rejected; no NautilusTrader PnL or NAV claim was made.

The failure is structural rather than a threshold problem: predicting another 10–20 bp after a burst cannot support the project under the reserved execution costs.

## v2: large-auction-event policy

v2 changes the unit of trade. A route is not created unless the pre-declared structural objective is at least 65 bp and next-open price risk remains between 10 and 90 bp. It separates three causal mechanisms:

1. **`RISK_BUILD_BREAKOUT`** — a compressed 60-minute auction breaks with new open interest, aligned aggressor flow, efficient displacement and at least one confirming peer. The target is the measured range projection; invalidation is material acceptance back inside the old range.
2. **`DELEVERAGING_CONTINUATION`** — a common large move occurs while open interest contracts, the move remains accepted through a pause and aggressor flow re-accelerates. The target is an extension of the forced-deleveraging leg.
3. **`FAILED_CASCADE_REVERSAL`** — a common deleveraging extreme fails, opposite aggressor flow appears and price reclaims toward the pre-event anchor. The extreme invalidates the route.

Everything else is `UNRESOLVED / NO TRADE`. Score only arbitrates simultaneous valid routes; it never changes the 3% risk budget.

## Causal and claim contracts

- Decisions use completed one-minute observations only.
- Price, aggTrade, depth, OI/positioning and premium observations retain their source ownership and publication delays.
- ATR, ranges and baselines exclude the candidate bar.
- All four symbols must share one exact minute clock.
- Entry geometry is rechecked at the next bar open; gaps that destroy the declared risk/objective are rejected.
- Repeated observations of one causal event are collapsed, and a global 120-minute event lockout is applied in the structural study.
- Future bars only label target/stop/timeout after the route is fixed. The study does not simulate orders, fills, positions, PnL or NAV.
- Only a stable positive development and validation result may proceed to an untouched NautilusTrader execution diagnostic.

## v2 study

`.github/workflows/candidate-37-v2-large-event-study.yml` builds checksum-owned rich observations for all four symbols, applies the unchanged policy to 2025-09 development and 2025-10 validation, and writes:

- `study.json` — split/state frequency, cost-after structural R labels, geometry and promotion verdict;
- `large_event_routes.csv` — one row per globally selected independent event;
- `console.log`.

## Reproduction

```bash
smc4 doctor
python -m unittest research/candidate-37/test_large_event_router.py -v
python research/candidate-37/v2_large_event_study.py \
  --input-root artifacts/c37-v2-downloaded \
  --output artifacts/c37-v2-study \
  --start 2025-09-01 \
  --end 2025-10-31 \
  --split 2025-10-01 \
  --horizon-minutes 180 \
  --lockout-minutes 120
```
