# Candidate 05 v29b–v32 result — completed causal families with no executable alpha

## Decision

**Discard v29b, v30, v31 and v32 as active candidates.** Their code and evidence
remain as reproducible negative research. None added an executable trade in the
first frozen BTC week, so none advanced to the second week.

Authoritative workflow run `31143861288`, artifact `8980977680`, commit
`3694c9682c0f404b3ba86f21b90768859a12b57d`.

Every candidate was compared with the identical v26 first-week control:

| Metric | v26 control |
|---|---:|
| Total return | +8.405430% |
| Geometric daily growth | +1.159644% |
| Trades / wins | 7 / 6 |
| Active days | 5 |
| Maximum drawdown | 3.084842% |
| Order rejections / denials / liquidations | 0 / 0 / 0 |

All four candidate totals were exactly identical to this control because their
new branches submitted zero orders.

## v29b — external displacement FVG first retest

```text
completed 4h external high/low
  -> accepted break with flow, efficiency and book withdrawal
  -> three-bar structural price gap
  -> first defended midpoint return
```

Diagnostics:

- 53 completed activity sessions and 106 external levels;
- 62 external accesses;
- one accepted external break;
- one three-bar gap;
- that state invalidated before a defended retest;
- zero retest confirmations and zero submissions.

The hypothesis was observable but too rare and did not produce an executable
opportunity. No performance claim can be made from the inherited v26 trades.

## v30 — remove only the FVG requirement

v30 is the required one-variable ablation of v29b. It kept the same completed
external level, acceptance state, current flow/depth confirmation, stop, target,
cost and risk contracts, but used the accepted level itself rather than
requiring a three-bar imbalance.

Diagnostics:

- the same one accepted external break;
- 12 post-break observations;
- acceptance invalidated before the first defended level retest;
- zero confirmations and zero submissions.

Removing the FVG condition did not create a trade. Therefore v29b was not merely
blocked by an overly strict gap definition; the only accepted break itself did
not persist to a tradable retest. The family has no structural improvement path
in the first frozen week and is discarded rather than loosened.

## v31 — impact and resiliency failure reversal

```text
accepted external shock
  -> price impact decays
  -> depleted book side replenishes
  -> price re-enters through level and shock midpoint
  -> first defended failed-level retest
```

Diagnostics:

- nine post-shock observation bars;
- zero impact-failure confirmations;
- the only shock remained accepted through the failure window;
- zero retest observations and zero submissions.

The useful result is causal: the observed external break did not become a failed
auction under the predeclared impact-decay and replenishment contract. Reversing
it would have contradicted the measured state. v31 is discarded, not converted
into a looser wick-reversal pattern.

## v32 — persistent queue pressure release

```text
three completed minutes of mirror-symmetric 2:1 depth pressure
  -> compact balance
  -> efficient breakout with flow, activity and opposing-depth withdrawal
  -> first defended frozen-boundary retest
```

Diagnostics:

- three qualifying compression/pressure states;
- zero confirmed release breakouts;
- zero watches, retests or submissions.

Displayed pressure did not become the required combination of aggressive flow,
price efficiency and opposing-depth withdrawal. Trading the depth signal alone
would violate the original premise and expose the system to spoofing or passive
liquidity which never causes repricing.

## Implementation integrity

Before this run, all four candidates' stale `ArmedEntryPath` constructor calls
were repaired to the current `created_ts` contract, and one repository-wide
constructor test now checks the exact dataclass field set. The fixed container,
full tests and NautilusTrader execution contracts passed. No candidate result
above is an implementation failure.

## Research lesson

The four families failed for **opportunity formation**, not for target choice or
execution timing. Their pre-entry state chains stopped before order submission.
Changing stops, targets, leverage, holding periods or execution style cannot
repair a scenario which never completes.

The next candidate therefore changes the cause rather than loosening the event:
a sequential likelihood-ratio detector asks whether repeated completed
aggressor-flow observations have entered a persistent regime, then independently
requires that regime to become efficient price discovery and survive its first
structural retest.
