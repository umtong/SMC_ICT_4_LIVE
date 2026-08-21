# Structural Auction Control v2

This branch does not preserve the earlier liquidity-auction policies or the
channel-only v1 policy as a benchmark to tune. It reuses causal data preparation,
price/volume footprints, natural geometry, NautilusTrader execution and account
accounting, while changing the unit of trading decision to one public-structure
auction lifecycle.

```text
confirmed wick structure and public liquidity
-> approach and interaction
-> completed rejection / acceptance / defended touch / failed-channel transition
-> first later OB/FVG or price-volume response
-> one immutable entry, structural invalidation and first causal destination
-> one global pending order or position
```

## Direction and liquidity

Trend lines and exact parallel channels are the primary directional and public
liquidity structures. A sweep and reclaim owns a rejection/fakeout episode. A body
break, outside hold and detached return owns acceptance. A mature four-point touch
may own continuation only after a later response. A channel which cannot recover
its midline may own failure only after the opposite transition completes.

OB and FVG are not standalone strategies. They refine the first tradable return
after a public-structure event. A plan without a channel/trend-line owner and a
later response is rejected.

## Episode ownership

The controller consumes the existing complete channel-control engine and the
integrated natural-geometry engine as event sensors. It then assigns one owner to
the public interaction. Simultaneous alternative labels and later labels emitted
while the same order opportunity is alive are suppressed. The choice is causal and
mechanism-based; no fitted score or hindsight best-plan label is used.

The destination is structural and selected by the inherited natural geometry
before reward/risk is considered. Among duplicate representations of the same
completed episode, the nearest valid structural destination is retained. If that
destination does not provide at least 1.0 gross R, there is no trade; the target is
not stretched to a fixed-R lattice.

## Trading contract

- BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT use one account and one global slot.
- Entry, stop and target are immutable before fill; no partial entry or exit.
- Position quantity risks approximately 3% of current NAV at the structural stop.
- Fees, entry slippage and stop slippage remain in the inherited Nautilus harness.
- An unfilled order may die with its causal opportunity; after fill only TP or SL
  closes the position. There is no clock exit, daily loss cap, trade quota, moving
  stop, PnL fallback or symbol-specific strategy.

`run_backtest_v2.py` runs this policy through the same event-driven execution and
continuous-account path as v1. The short workflow is for trade/no-trade diagnosis,
not a separate promotion or scoring system.
