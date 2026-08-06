# Candidate 06 v2.0 — Accepted-Auction Defense-Origin Mitigation (ADOM)

## Pre-execution status

`PREDECLARED / NOT YET EVALUATED`

This ledger freezes the causal hypothesis, comparison, implementation invariants, and discard rules before ADOM results are known.

## Evidence motivating the candidate

The strongest completed-auction continuation references on the first BTC week were:

| reference | geometric NAV/day after cost | trades | wins | PF | max DD |
|---|---:|---:|---:|---:|---:|
| completed 60m SAC + next directional-body defense | +0.9283% | 5 | 2 | 1.6905 | 11.47% |
| completed 30m SAC + next directional-body defense | +0.9409% | 5 | 2 | 1.6831 | 10.07% |
| 30m confirmation with favorable-drift veto removed | -1.2316% | 10 | 2 | 0.6531 | 15.38% |
| completed 15m SAC + next directional-body defense | -1.0575% | 9 | 3 | 0.6050 | 13.70% |

Thus the next-bar defense contained useful directional information, while simply admitting delayed/chased entries or shortening the auction horizon destroyed expectancy.

For the 30m reference, diagnostics recorded five entries, thirteen `FAVORABLE_MOVE_ALREADY_CONSUMED` abstentions, one `NET_REWARD_RISK_ERODED_AFTER_DELAY` abstention, four delayed prices outside the bracket, and twelve failed defenses. The structural bottleneck is post-confirmation execution, not a shortage of detected accepted auctions.

## Causal market hypothesis

A completed 30-minute boundary acceptance, its first held retest, and a separate completed directional-defense bar establish a continuation state. The defense bar itself is a confirming displacement. Entering at its close often chases an already consumed path; removing confirmation admits poor auctions.

ADOM therefore does not remove confirmation. After the defense bar completes, it places one passive order at the **defense-bar open**, the observable origin of the confirming displacement. The order remains valid only until the fixed 30-minute auction containing the signal ends. A fill represents mitigation of the confirming impulse while the same auction state is still active.

The scenario is invalid for entry when the structural objective was already touched during the defense bar. Missing the mitigation before auction expiry is an abstention, not a market chase.

## Fixed state sequence

```text
prior 30m auction completes
-> next auction sweeps a completed boundary
-> displacement accepts beyond the boundary
-> first retest holds the accepted boundary
-> next completed 1m bar holds the boundary and closes directionally
-> objective has not already been reached
-> passive GTD entry at that defense bar's open
-> entry expires at the next fixed 30m boundary if unfilled
-> unchanged structural stop and projection objective after fill
```

## Detector / scenario / execution separation

- **Detector:** existing `FixedIntervalAuctionLiquidityRelayEngine`, completed bars only.
- **Defense:** existing `DIRECTIONAL_BODY` next-completed-bar check.
- **Entry-placement detector:** `resolve_entry_placement`, using only the completed defense bar and fixed auction clock.
- **Execution:** NautilusTrader native OTO/OUO bracket with a GTD LIMIT parent, GTC structural stop and target children, native fills, positions, fees, and NAV.

No future observation, trade outcome, or PnL enters the placement decision.

## Controlled one-variable comparison

| variant | post-defense entry | selectable |
|---|---|---|
| `adom_defense_origin_limit` | passive limit at completed defense-bar open, GTD to fixed-auction boundary | yes |
| `adom_market_after_defense_reference` | existing immediate market entry at completed defense-bar close | no; diagnostic reference |

Unchanged between variants:

- BTCUSDT and frozen week;
- completed non-overlapping 30-minute auctions;
- sweep threshold, acceptance displacement, first retest, and next directional-body defense;
- failed-defense action `ABSTAIN`;
- structural stop, objective, cooldown, and one global slot;
- whole-account NAV with fixed 3% planned-loss risk;
- effective fee rate, one-tick adverse execution assumption, probabilistic limit-touch fill model;
- NautilusTrader orders, fills, positions, margin, and NAV accounting.

The unchanged reference must reproduce committed prior Nautilus metrics. Divergence is an implementation regression, not strategy evidence.

## Frozen evaluation order and gate

1. BTC `2024-02-26` through `2024-03-04` UTC;
2. BTC `2024-09-23` through `2024-09-30` UTC;
3. BTC `2024-04-22` through `2024-04-29` UTC.

The full variant alone may advance, without code or parameter changes, only after satisfying every first-week gate:

- cost-after geometric NAV/day `>= 1.00%`;
- trades `>= 10`;
- win rate `>= 45%`;
- positive trades `>= 5`;
- max NAV drawdown `<= 25%`;
- largest positive trade share `<= 40%`;
- valid Nautilus evidence and positive expectancy.

Long evaluation is unauthorized unless the unchanged candidate passes all three weeks.

## Implementation-error rules

Implementation failure includes:

- registration-anchor, syntax, import, or pure causality test failure;
- unchanged market reference failing deterministic regression;
- unsupported native LIMIT/GTD bracket construction;
- parent expiry not clearing the global pending-order slot;
- post-only order becoming marketable at submission;
- missing Nautilus metrics, order rejection caused by code, or unresolved orders at evaluation end.

Only the defective implementation may be corrected before rerunning the same frozen week and unchanged hypothesis.

## Logic-error and discard rules

With valid implementation, discard ADOM after this one-variable comparison when:

- mitigation fills produce non-positive expectancy or PF below 1;
- too few confirmed auctions revisit the defense origin before causal expiry;
- fills occur often but continuation win rate remains below the gate;
- loss clustering exceeds the drawdown gate;
- the full variant does not improve the opportunity/expectancy tradeoff over the market reference;
- any unchanged sealed holdout fails after a first-week pass.

No alternative limit fraction, longer expiry, session filter, target retuning, stop retuning, or auction-period search is allowed after observing results.

## Retainable information if discarded

- whether strong defenses revisit their origin before the auction state ends;
- the fill/expiry distribution of causal mitigation orders;
- whether market-chase winners survive passive mitigation;
- whether the defense origin is a real continuation support state or merely a descriptive candle level;
- reusable native GTD bracket lifecycle infrastructure, without treating it as alpha evidence.
