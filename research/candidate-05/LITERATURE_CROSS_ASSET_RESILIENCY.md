# Literature note — cross-asset order flow, common price discovery and resiliency

## Research question

When one crypto perpetual consumes completed-session liquidity while correlated
peers do not, is the local move a temporary liquidity excursion or part of
common information-driven price discovery?

The v37 result shows that peer non-confirmation by itself cannot answer that
question. This note records the external basis for v38's distinction between
`isolated local raid` and `common continuation`.

## Primary sources

### Cont, Cucuringu and Zhang — Cross-Impact of Order Flow Imbalance in Equity Markets

- arXiv: https://arxiv.org/abs/2112.13213
- Multi-level integrated order-flow imbalance explains contemporaneous impact
  better than a best-level measure.
- Lagged cross-asset OFI improves short-horizon return forecasts, but the effect
  decays rapidly.

Research use: peer state is useful as short-lived context, but it should not be
converted into a stable cross-impact coefficient or a long-lived directional
score.

### Capponi and Cont — Multi-Asset Market Impact and Order Flow Commonality

- SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3706390
- The paper separates order-flow commonality from an unrestricted matrix of
  pairwise cross-impact coefficients.
- Empirical cross-impact terms may be unstable and may change sign, which makes
  a fitted pairwise coefficient difficult to interpret causally.

Research use: v38 uses a small state machine — unanimous session
non-confirmation plus absence of common efficient continuation — rather than
fitted symbol-pair weights.

### Anastasopoulos, Gradojevic, Liu, Maynard and Tsiakas — Order flow and cryptocurrency returns

- Journal of Financial Markets 79 (2026), article 101047
- DOI: https://doi.org/10.1016/j.finmar.2026.101047
- The paper distinguishes a transitory order-flow component associated with
  short-term reversal from a permanent component associated with price
  discovery.

Research use: a local sweep should be faded only after the common permanent
component is absent. Price and flow agreement across peers is evidence against a
purely local transitory interpretation.

### Xu et al. — Limit-order book resiliency after effective market orders

- arXiv: https://arxiv.org/abs/1602.00731
- The study documents different post-shock paths: more aggressive market orders
  are followed more often by price resiliency, while less aggressive orders show
  more continuation, alongside spread/depth recovery.

Research use: the local market still needs its own reclaim, tail-flow/depth turn
and displacement. Cross-asset isolation alone is not a reversal order.

## v38 mechanical interpretation

```text
SESSION NON-CONFIRMATION
    all three peers fail the corresponding completed-session raid

COMMON-CONTINUATION TEST
    a peer counts as continuing common price discovery only when:
      proposed reversal side * peer 60-second return < 0
      proposed reversal side * peer 60-second flow <= -existing flow threshold
      peer 60-second price efficiency >= existing CHoCH efficiency threshold

ISOLATED CONTEXT
    fewer than two peers meet that continuation definition

LOCAL ACTION
    local boundary reclaim
    + final-tail flow improvement
    + current directional depth
    + displacement / CHoCH
```

The thresholds are existing causal contracts, not fitted v38 parameters. The
experiment changes the market-state interpretation and entry transition; it does
not change risk fraction, fees, slippage, leverage, stop distance or target
selection.
