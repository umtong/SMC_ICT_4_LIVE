# Candidate-02 v93 — Cross-market liquidity-shock resilience

## Why v92 was not repaired

v92 correctly found several local reactions after an external sweep, but it
incorrectly treated those reactions as proof of delivery through the whole
preceding eight-hour range. Three of five ablation trades exceeded +1.5R before
ultimately failing, and none reached the opposite range boundary.

v93 therefore treats local rebalancing and full breakout delivery as different
states. It is a new prospectively tested candidate, not a second v92 ablation.

## State machine

### Shared event detector

1. Freeze the completed eight-hour high and low at 00:00, 08:00 or 16:00 UTC.
2. Observe a turnover-qualified sweep of exactly one boundary.
3. Consume that boundary once; repeated touches are not independent liquidity
   events.
4. Observe the next three completed minutes and classify one of two mutually
   exclusive states.

### Local reversion

The perpetual closes back inside the old range. Opposite displacement must
break the five-minute pre-sweep internal structure and leave a causal
three-candle FVG. Entry occurs only after a later completed minute retraces into
that gap and rejects its midpoint.

The target is the nearest confirmed five-minute pivot in the trade direction
which was known before the sweep, has not subsequently been closed through,
and pays at least 1.10 cost-after reward/risk. This is an internal liquidity
transfer, not an assumption of full range traversal. Invalidation remains beyond
the sweep extreme.

### Common breakout continuation

At least two closes remain outside the range, the last close is a material ATR
fraction beyond it, spot also crosses the basis-adjusted boundary, and basis
expansion is not the dominant source of the perpetual move. Same-direction
displacement must leave an FVG. A later retrace must touch that gap and still
close outside the old range.

The stop is just inside the old boundary, where breakout acceptance is invalid.
The target is the nearest intact external five-minute pivot from the preceding
48 hours which was known before the sweep and pays the same minimum cost-after
reward/risk.

### Ambiguous state

No trade is allowed when range acceptance and spot/perpetual participation do
not identify exactly one state, when no intact pivot objective exists, or when
the objective cannot pay realistic costs.

## Prospective evaluation

The first BTC week was selected before direct data collection with seed
`2026080693`: 2025-08-18 through 2025-08-25 UTC.

The 20-minute retrace horizon is central; 15 and 25 minutes are adjacent
structural checks. Local-reversion and common-breakout modes are run separately
for attribution. All executions and NAV transitions belong to NautilusTrader
1.230.0 with current-account-NAV 3% risk and no nominal cap or score multiplier.
