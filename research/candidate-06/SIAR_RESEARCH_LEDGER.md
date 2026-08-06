# Candidate-06 SIAR research ledger

## Decision inherited from AFHR

AFHR is rejected as a complete trading candidate.  Its one-time unchanged
2024-09-23 holdout ablation produced no positive-growth variant:

- parent HML reference: -4.7984% geometric NAV/day, 19 trades, 31.16% MDD;
- freshness only: -3.9662%/day, 17 trades, 29.03% MDD;
- prior range/volume quality only: -2.5469%/day, 16 trades, 18.59% MDD;
- quality plus freshness: -0.1651%/day, 5 trades, 6.87% MDD.

The conjunction substantially suppresses repeated trades from weak and stale
contexts, but it does not create positive expectancy and does not improve the
third sealed week.  Retain the completed-close freshness invalidation and the
lesson that activity magnitude and context age interact; do not tune AFHR's
quantiles, body threshold or stale duration.

## New structural claim

The next candidate replaces the higher-timeframe flow definition instead of
adding another filter.

1. Compute signed aggressive-flow intensity from completed 60-minute bars as
   `(2 * taker_buy_volume - volume) / median(prior completed volume)`.
2. Estimate expected flow with the median of a sealed prior window.
3. Treat only the direction-aligned residual as fresh flow information.
4. Require the residual to be exceptional to the prior absolute-residual
   distribution when the surprise factor is enabled.
5. Compare direction-consistent close displacement per unit residual flow with
   the prior median.  Sub-median response is absorption, not continuation.
6. Keep the confirmed 5-minute swing/equal pool, counter-bias sweep, separate
   response, structural bracket, fees, fills and 3% NAV risk unchanged.

This follows three primary microstructure findings without pretending that the
bar proxy is full order-book OFI:

- Cont, Kukanov and Stoikov: short-horizon price changes are more robustly
  related to order-flow imbalance than trade volume, with impact depending on
  depth.
- Taranto et al.: realised order-flow deviations from expected flow carry the
  history-dependent impact information.
- Taranto, Bormetti and Lillo: predictable order flow is offset by adaptive,
  asymmetric liquidity; raw persistent flow need not move price.

## Controlled variants

- `siar_full`: surprise plus impact efficiency;
- `siar_surprise_only_ablation`: remove only efficiency;
- `siar_impact_only_ablation`: remove only the exceptional-surprise threshold;
- `siar_freshness_reference`: both new factors off, ineligible reference.

No time-of-day fitting, direction-only switch, threshold sweep, score-based risk
or execution change is permitted.  A first-week pass only authorizes unchanged
replay on the two sealed BTC weeks.
