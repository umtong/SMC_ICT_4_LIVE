# Failed-reversal continuation observation — discarded before strategy implementation

## Decision

**Do not implement failed-reversal continuation as a trading candidate.** The
hypothesis was examined only as a completed-bar market-state diagnostic on the
11 frozen v38 branch trades. No order, fill, fee, position, account or PnL logic
was added to the observation tool.

The diagnostic found that price eventually closed back beyond the original
sweep extreme in every one of the seven losing v38 cases, but none completed the
predeclared causal continuation state and none produced a first retest defended
by both current flow and displayed depth. Price reacceptance alone is therefore
only a chart pattern, not a validated continuation scenario.

Authoritative observation workflow: GitHub Actions run `31162727102`, source
commit `fd267eb05efb25c610e3bf0d414266613b609750`, artifact `8987787116`.
The observation and component counts completed successfully. The workflow's
final evidence-verification step failed only because the payload retained the
v2 compatibility schema label despite containing all component-cascade fields.
That evidence-label implementation error was repaired separately without
changing any observation or predicate.

## Frozen population

The population was not selected after viewing continuation results. It was the
complete set of 11 v38 incremental trades from the three frozen shared-account
weeks:

- 7 original v38 losses;
- 4 non-negative original v38 outcomes;
- identical event timestamps, sweep extremes, ATRs and position-close times
  frozen in `v38_trade_cases.json`.

Observation began after the original reversal CHoCH. The original raid
direction was tested as the proposed continuation side.

## Strict state tested

```text
completed close beyond original sweep extreme
+ directional candle body
+ aligned completed 15-second aggressor flow
+ aligned completed 60-second aggressor flow
+ existing acceptance efficiency threshold
+ existing activity threshold
+ threatened-side depth withdrawal
+ close at the continuation-side end of the bar
-> first later touch of the reaccepted sweep extreme
+ close defense
+ current flow and depth defense
```

All thresholds were existing strategy contracts. None was fitted to these 11
cases.

## Results

### Losing original v38 cases

| Observation | Count |
|---|---:|
| Cases | 7 |
| Any later price reacceptance | 7 |
| Price reacceptance before original v38 position closed | 3 |
| Strict causal reacceptance | 0 |
| First retest close-defended | 2 |
| First retest fully defended by flow and depth | 0 |

The cumulative completed-bar cascade was:

| Sequential condition | Bars surviving all conditions through this step |
|---|---:|
| Price reaccepted | 559 |
| + directional body | 274 |
| + 15-second flow | 153 |
| + 60-second flow | 121 |
| + price efficiency | 6 |
| + activity | 2 |
| + threatened-side depth withdrawal | 0 |
| + close location | 0 |

The two close-only defended first retests were not executable evidence. Neither
had flow/depth defense. Their median 15-bar favorable excursion was 2.82 sweep
ATR, but median adverse excursion was larger at 3.57 ATR. At 30 bars the median
favorable excursion increased to 4.46 ATR while the same 3.57 ATR adverse path
remained. With only two observations and no microstructure defense, this cannot
support a realistic 3% loss-budget trade.

### Non-negative original v38 cases

| Observation | Count |
|---|---:|
| Cases | 4 |
| Any later price reacceptance | 2 |
| Price reacceptance before original v38 position closed | 0 |
| Strict causal reacceptance | 0 |
| First retest close-defended | 1 |
| First retest fully defended by flow and depth | 0 |

Thus later price reacceptance was more common after losing reversals, but the
causal transition required for a continuation order was absent in both groups.

## Implementation versus logic

### Implementation finding

The first diagnostic version used one field for both movement after the
reacceptance bar and movement after a defended retest. It correctly stopped
following the first failed touch, but the field name made the two reference
points ambiguous. The v2 schema separated:

- `reacceptance_reference_price` / `reacceptance_excursions`;
- `retest_reference_price` / `retest_excursions`.

A later wrapper repairs only the component-cascade schema label. These are
observation-evidence repairs, not market-logic changes.

### Logic finding

The failed-reversal continuation idea failed before strategy implementation.
The missing state was not merely a distant target or a timing parameter:

- price reacceptance occurred;
- aligned flow occasionally occurred;
- efficiency almost always removed the event;
- when efficiency survived, activity and then book withdrawal removed the
  remainder;
- no first retest received full current flow/depth defense.

Relaxing efficiency, activity or depth requirements after observing this
cascade would redefine ordinary price reacceptance as information-driven price
discovery. That would be retrospective threshold fitting, so it is not done.

## Retained lesson

A losing reversal does not automatically become a tradable continuation. The
machine must observe a new cause, not infer one from the previous position's
loss. Original-direction price reacceptance is useful as an invalidation and
regime-change observation, but it is not sufficient for entry.

The next research family should use an independent state variable capable of
separating liquidation/deleveraging from new position building. Binance's
causally delayed open-interest metrics are already available in the project and
can be observed without changing the NautilusTrader execution or account
infrastructure. The next step is therefore an observational positioning-state
diagnostic, not a relaxed continuation strategy.
