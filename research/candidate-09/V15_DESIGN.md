# Candidate 09 v15 — failed-auction impact classification (prepared, not promoted)

## Status

Prepared after v13/v14 identified `boundary-stop-all` as the strongest controlled
invalidation rule. v15 is **not an evaluated or active baseline**. It remains dormant
until the frozen v14 pooled and three-year NautilusTrader evaluation completes.

## Evidence carried forward

- Preserve the accepted-breakout-failure detector, entry observation time, failed-boundary
  stop, source-range equilibrium target, full cost model, and 3% NAV loss budget.
- Do not restore the repeatedly weak continuation branch, fixed-session sweep family,
  retest market chase, or passive boundary-limit salvage.
- v13 showed that applying failed-boundary invalidation consistently was stronger than
  mixing accepted-extreme and failed-boundary stops. v14 promotes that ablation and fixes
  evaluation semantics; v15 does not modify those contracts.

## Structural question

A completed auction can fail for economically different reasons even when the same candle
closes back through the accepted boundary. The detector should therefore classify the
failure before the trading scenario consumes it.

### Passive absorption

Price re-enters the old range while cumulative aggressive flow since the breach is still
aligned with the failed breakout. The price/flow disagreement is evidence that passive
liquidity absorbed the aggressive side.

### Active liquidity flip

Cumulative flow has reversed. The failure is accepted only when its ATR-normalized price
movement per unit opposite failure-bar flow is at least as large as the original
acceptance's ATR-normalized movement per unit aligned cumulative flow.

```text
acceptance impact efficiency
= outside close distance / acceptance ATR / aligned acceptance flow

failure impact efficiency
= inside close distance / failure ATR / opposite failure-bar flow

active liquidity flip
= cumulative flow reversed
  and failure impact efficiency / acceptance impact efficiency >= 1
```

The comparison is dimensionless and event-relative. No return-optimized numerical
threshold is introduced; `1` means only that the failure response is no weaker than the
acceptance response within the same scenario.

## Frozen variants

- `baseline`: passive absorption OR active liquidity flip.
- `no-impact-classification`: exact v14 economic behavior with diagnostics only.
- `passive-absorption-only`: isolates price re-entry against residual breakout flow.
- `active-impact-flip-only`: isolates reversed flow with relative impact dominance.

## Causality and implementation contract

- Acceptance statistics are frozen on the completed acceptance bar.
- Failure statistics use only the completed failure bar and cumulative state already
  observed at that timestamp.
- No future target touch, MFE, MAE, outcome label, or later book state enters the signal.
- NautilusTrader remains the only execution/accounting engine when this proposal is
  activated.
- Synthetic contracts cover both directions, mechanism separation, rejection of a weak
  active reversal, and exact v14 control behavior.

## Activation rule

Activate and evaluate v15 only if v14 completes without implementation error and its
predeclared three-year result identifies conditional-edge quality or drawdown as the main
failure. If v14 instead fails primarily on opportunity rate, this filtering proposal is
not the appropriate next step; the next hypothesis must add an independent causal scenario
rather than further reduce entries.

## Research basis

The design follows the microstructure distinction between order-flow pressure and the
liquidity-dependent price impact of that pressure, including asymmetric liquidity and
order-book resilience. Relevant primary references include Cont, Kukanov & Stoikov,
*The Price Impact of Order Book Events*; Taranto, Bormetti & Lillo, *The Adaptive Nature of
Liquidity Taking in Limit Order Books*; and Bechler & Ludkovski, *Order Flows and Limit
Order Book Resiliency on the Meso-Scale*.
