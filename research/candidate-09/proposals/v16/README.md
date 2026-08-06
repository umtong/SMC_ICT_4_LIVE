# Candidate 09 v16 — unaccepted liquidity-sweep absorption (dormant proposal)

## Status

Prepared while GitHub Actions was experiencing a major outage. This proposal is **not the
active strategy and has not been economically evaluated**. The active priority remains the
frozen v14 pooled screen and predeclared 2022-01-01 through 2025-01-01 NautilusTrader BTC
evaluation.

Activate v16 only if v14's long evaluation fails primarily because of opportunity rate or
active-month coverage. If v14 instead fails because of conditional edge quality or drawdown,
activate the separately preserved v15 price-impact classification rather than adding entries.

## Success and failure evidence carried forward

- Accepted-breakout failure reversal is the only retained v4/v10 entry family. Repeatedly weak
  continuation, fixed-session sweep, market-retest chase, and boundary-limit salvage remain
  discarded.
- v13 showed that failed-boundary reacceptance is a stronger causal invalidation than the full
  accepted excursion. v14 promotes that rule without modifying entry time or equilibrium target.
- A distinct detector state remains unused: a breach that re-enters the source range before the
  required second outside close. v13 correctly expired it because it was not an accepted breakout,
  but that does not imply it is economically neutral. It is a different scenario: an **unaccepted
  liquidity sweep**.

## Causal sequence

```text
completed 15m / 60m / daily auction extreme
→ directional approach pressure
→ meaningful breach excursion
→ first post-breach completed bar returns through the level
→ rejection body meets the existing reversal-displacement contract
→ cumulative aggressive flow across breach + rejection still points outward
→ therefore price moved inward against residual aggressive flow: passive absorption
→ market reversal at completed rejection close
→ invalidation beyond observed sweep extreme + existing ATR buffer
→ target source-auction equilibrium
→ unchanged full-cost net reward/risk >= 1.2
→ unchanged current-NAV 3% planned-loss sizing in NautilusTrader
```

The branch does not call this an accepted-breakout failure. Detector and scenario labels remain
separate. The one-bar condition is semantic rather than fitted: it distinguishes an immediate
liquidity sweep from a multi-bar attempted acceptance. Later re-entry remains exact v14 behavior.

## No new fitted thresholds

Every numerical requirement already exists in v14:

- `minimum_excursion_atr`
- `minimum_volume_ratio`
- `failure_close_buffer_atr`
- `minimum_resolution_displacement_atr`
- `stop_buffer_atr`
- `minimum_net_reward_to_risk`
- `composite_taker_cost_per_fill`

The only new condition is the sign disagreement:

```text
UP sweep: cumulative post-flow > 0 while price closes back inside
DOWN sweep: cumulative post-flow < 0 while price closes back inside
```

A positive magnitude is not fitted. The sign states only that price rejected the sweep despite
net aggressive flow still favoring it, which is the operational definition of passive absorption.

## Frozen controls

- `baseline`: v14 plus the unaccepted absorption scenario.
- `no-unaccepted-absorption`: exact v14 economic behavior.
- `no-residual-flow`: removes only price/flow disagreement.
- `no-volume-participation`: removes only the existing participation requirement.

## Completed-event diagnostic, not a backtest

A reproducible script reads only completed v13 event records and source-auction metadata. It does
not read future bars, stop/target touches, PnL, MFE, MAE, or position outcomes.

- pre-acceptance re-entries: 774
- immediate one-bar re-entries: 687
- all frozen structural and cost geometry conditions: 9
- distribution by fixed week: 7 / 1 / 1
- distribution by horizon: 15m = 2, 60m = 5, daily = 2
- direction: UP = 6, DOWN = 3
- overlap with v13 baseline open positions at observation time: 0

These nine are only potential signals. Their realized performance must be evaluated by the native
NautilusTrader adapter under the global one-order-or-position invariant before v16 can be judged.
The 7/1/1 concentration is a known warning, not evidence of robustness.

## Known failure conditions

Discard this branch if any of the following occurs in its frozen test:

- the exact v14 control is stronger after costs;
- opportunities remain concentrated in the 2022 week or a small number of daily levels;
- `no-residual-flow` is equally strong or stronger, showing that absorption attribution is not causal;
- the branch adds frequency but worsens expectancy or drawdown enough to reduce NAV growth;
- long-run activity remains sparse despite the additional scenario.

## Research basis

The economic interpretation follows primary limit-order-book research showing that short-horizon
price response depends on order-flow imbalance **and** available/resilient liquidity, so price can
move weakly or reverse despite continued aggressive flow when passive liquidity absorbs it. The
relevant framework is consistent with Cont, Kukanov & Stoikov, *The Price Impact of Order Book
Events*; Taranto, Bormetti & Lillo, *The Adaptive Nature of Liquidity Taking in Limit Order Books*;
and Bechler & Ludkovski, *Order Flows and Limit Order Book Resiliency on the Meso-Scale*.
