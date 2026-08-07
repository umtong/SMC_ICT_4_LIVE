# Candidate 09 v24 — index-anchored liquidation-dislocation reversion

## Economic question

A completed five-minute open-interest reduction is not directional evidence by itself.
The candidate asks whether the traded USD-M perpetual moved materially farther than its
completed Binance index-price auction.  Only a self-normalized OI reduction, directional
perpetual displacement/participation/aggregate taker flow, and an abnormal
futures-minus-index return and basis displacement form the candidate event.

The trade is not entered at the shock.  Within four completed one-minute bars the basis
must contract by at least 25%, the perpetual must break the preceding one-minute internal
structure in the reversion direction with opposing aggregate flow, and the index must not
extend the liquidation extreme.  Entry is then toward the frozen pre-shock fair basis.
The stop lies beyond every perpetual extreme observed from the pulse through confirmation.

## State order

```text
completed 15m source auction
→ completed 5m OI reduction
→ perpetual displacement beyond source edge
→ abnormal perpetual/index return gap and basis dislocation
→ basis-reclaim pending
→ completed internal structure/flow shift while index does not extend
→ reversal toward frozen pre-shock fair basis
```

Any fair-basis target reached before confirmation, failure to reclaim within four bars,
invalid stop/target geometry, or cost-after reward below 1.2R ends the scenario without a
trade.

## Frozen controls

- `no-oi`: remove only OI admission; preserve index gap and reclaim.
- `no-index-gap`: remove only futures/index dislocation admission; preserve OI and reclaim.
- `no-reclaim`: enter at metric availability close; preserve OI and index gap.

No parameter optimizer is present.  Fixed weeks, full composite execution cost, NAV-based
3% planned loss, target/stop accounting, pooled gate and conditional three-year BTC
interval are unchanged from v23.

## Known limitations and failure conditions

- Binance index-price klines are a fair-value anchor, not an executable spot quote.  All
  fills remain on the perpetual and pay the full composite cost.
- Five-minute OI is an aggregate positioning snapshot, not a trader-level liquidation
  label; the index dislocation is required precisely to avoid treating every OI move as a
  cascade.
- Same-minute futures and index bars are exposed only after both complete.  Metrics are
  exposed one completed minute after `create_time`; faster normalizations are deliberately
  missed.
- One-minute bars cannot reconstruct queue depth or nonlinear market impact.  A gate pass
  therefore remains only a screen and must survive the frozen three-year evaluation.
