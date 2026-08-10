# Candidate-02 v92 — Session liquidity sweep, displacement and FVG retrace

## Research question

Can a machine identify the full SMC/ICT sequence

`external liquidity -> sweep -> failed auction -> CHoCH/BOS displacement -> FVG -> retrace -> opposing liquidity`

without reducing it to an isolated candle pattern?

The system does **not** trade because a sweep or FVG exists. Those are event
detectors. A trade intent exists only after the ordered state sequence is
complete.

## Economic and market-structure basis

* Osler's dealer-order evidence links support/resistance behavior to clustered
  stop-loss and take-profit orders. A range extreme is therefore treated as a
  plausible external liquidity pool, not a magical chart line.
* Stop-loss cascades can intensify a break. v92 does not fade every break:
  acceptance outside the range is an explicit no-trade state.
* Order-flow imbalance moves prices in relation to available depth. The sweep
  must contain aggressive flow in the swept direction, while the later failure,
  structure break and imbalance retrace determine whether that flow was trapped.
* Bitcoin and Ether exhibit stable hour-of-day and within-hour liquidity
  periodicity. The 00:00, 08:00 and 16:00 UTC cycle anchors are frozen before
  data collection and represent completed eight-hour dealing ranges.

## Mechanical definitions

### External liquidity

For each locked cycle anchor, the preceding completed eight hours define a
frozen high and low. These levels cannot move after the active window begins.

### Sweep

A completed minute breaches exactly one frozen boundary by at least 0.03 prior
ATR, with directional aggressive-flow ratio above both 0.08 and its shifted
prior quantile, and turnover above its shifted prior median.

### Failed auction

The market must close back inside the frozen range within three completed
minutes. A break that remains accepted outside is not a reversal setup.

### CHoCH / displacement

Within five completed minutes after reclaim, the opposite-direction body must
exceed both 0.25 prior ATR and its shifted prior body quantile, then close beyond
the high or low of the five completed minutes preceding the sweep.

### Fair-value gap

The displacement must leave a causal three-candle gap of at least 0.02 prior
ATR. The gap is stored as a zone, not used as a standalone entry signal.

### Entry

Within the locked 15/20/25-minute retrace horizon, a later completed minute must
touch the gap and close back on the trade side of its midpoint. Aggressive flow
may be mildly adverse but cannot be strongly adverse. Entry is a market order
after that minute closes.

### Invalidation and objective

The stop is beyond the sweep extreme plus 0.05 prior ATR. The target is the
opposite frozen dealing-range boundary. A setup that cannot pay realistic entry
and exit costs with cost-after reward/risk of at least 1.0 is no trade.

## Prospective validation

The first BTC week was selected before collection with seed `2026080692`:
`2024-03-11T00:00:00Z` through `2024-03-18T00:00:00Z`.

The central rule is a 20-minute retrace deadline. Fifteen and twenty-five
minutes are adjacent structural checks, not a broad parameter search. The
three cycle-only configurations diagnose whether one clock period monopolizes
the result.

All fills, commissions, positions and NAV are owned by NautilusTrader 1.230.0.
Risk is current account NAV times 3% divided by expected per-unit loss including
fees, slippage, impact and funding. No nominal cap or score risk multiplier is
allowed.
