# Candidate-09 v21 — counterflow-absorption auction expansion

## Failure evidence carried forward

v18's exact fixed-week control exceeded the short gate but reduced 100,000 USDT to
8.86 USDT over the predeclared three-year BTC evaluation. v19 showed that waiting for a
failed boundary to retest was usually too late. v20 formed value from a completed auction,
but every baseline return either reached equilibrium before entry, was reaccepted outside,
expired, or failed the cost-after reward geometry. Removing the retest created only three
trades and a pooled loss.

The repeated failure is not a missing mean-reversion filter. Loss of acceptance and return
to value do not establish durable opposing liquidity under one-minute aggregate data.

## New causal question

Can a completed auction expand when aggressive counterflow fails to push price back into
the source range?

```text
fully completed 15-minute source auction
→ later edge breach
→ two completed outside closes
→ displacement + participation + aligned aggressor flow
→ opposing aggressor-flow pullback touches the old edge
→ price nevertheless closes outside the source
→ aligned displacement re-expands through the pullback extreme
→ continuation into the adjacent auction range
```

The divergence between opposing taker flow and defended price is a coarse absorption
hypothesis. It does not claim to observe L2 replenishment, hidden orders or queue priority.

## Source auction

A baseline source is a fully completed 15-minute range whose closing price lies in the
central half of that range. This means both edges formed before the next observation and
the source did not finish as a one-sided directional bar cluster. The control
`no-balanced-source` admits every completed 15-minute range.

## Entry, target and invalidation

- Entry: completed re-expansion close after an absorbed pullback.
- Invalidation: beyond the old source edge and the observed pullback extreme, plus the
  pre-existing ATR stop buffer.
- Objective: one frozen source-range width beyond the accepted edge. This represents
  migration into an adjacent auction, not a fitted risk multiple.
- A signal is rejected unless entry, stop, target and both modeled fills retain at least
  the unchanged cost-after 1.2 reward-to-risk.
- If the adjacent-auction objective is reached before entry, the scenario expires without
  chasing.

## Frozen one-variable controls

| Variant | Single changed causal layer |
|---|---|
| `baseline` | balanced source + counterflow absorption + re-expansion |
| `no-absorption` | price-only defended pullback; opposing flow not required |
| `no-reexpansion` | enter on the absorbed pullback close |
| `no-balanced-source` | permit directional completed source auctions |

No detector threshold, cost, risk fraction, fixed week, long interval or NautilusTrader
execution/accounting contract changes.

## Predeclared interpretation

- If `no-absorption` dominates, one-minute taker-flow divergence is not a useful proxy for
  passive support and must be removed rather than tuned.
- If `no-reexpansion` dominates, the extra confirmation is a timing cost.
- If `no-balanced-source` dominates, balance is not necessary for adjacent-auction
  expansion.
- If all variants fail or remain sparse, this family is discarded without adding more
  price-impact thresholds.

## Literature relationship

Short-horizon price changes are linked to order-flow imbalance conditional on available
market depth, while limit-order additions/cancellations and deeper book shape often carry
more predictive information than trade imbalance alone. v21 therefore treats the
price/flow divergence as a falsifiable proxy, not as observed book replenishment. The
state sequence is designed to fail cleanly if aggregate taker flow lacks enough information.
