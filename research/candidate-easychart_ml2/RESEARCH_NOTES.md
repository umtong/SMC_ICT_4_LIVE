# EasyChart ML2 research notes

## Market interpretation

The source material gives different tools different responsibilities. Trend lines and channels describe direction, pace, range and potential transition points. OB/FVG describe event-local footprints and response zones. Fakeout/trap describes a liquidity event: a known boundary is crossed, liquidity is taken, and price either reclaims or accepts outside. These are not independent buttons and are not equally informative in every latent state.

The executable abstraction is therefore:

```text
meaningful location
→ liquidity/acceptance/rotation episode
→ first causal response
→ structural stop
→ nearest pre-existing unspent objective
```

The model is not asked to learn this geometry from labels. It receives a complete plan and judges whether the observed context and response make target-before-stop likely enough to grow fixed-risk NAV after costs.

## Implementation errors separated from logic errors

The first ML path had several implementation faults which invalidated any strategic conclusion:

1. A local 15/5/1 mature-diagonal engine received a parent 60-minute bar and raised before plans could be evaluated.
2. Feature extraction read nonexistent zone-strength names and silently emitted zeros instead of `higher_strength_ratio`, `lower_strength_ratio`, `trigger_strength_ratio`.
3. Parsing process-global `rule_provenance` made unrelated plans appear to contain every imported mechanism.
4. Missing target lookup could be interpreted as a fresh target.
5. Broad context removed examples inside lower engines before the final router, creating selected-sample bias.
6. The prior expected-R threshold was inconsistent with repeated fixed-fraction compounding; ML2 now uses expected log NAV growth at 3% risk.
7. Chunk-local plan counters collide when independent research processes are merged; merge now namespaces plan and causal-event identities by source checksum.

These are code/contract problems, not evidence that the market ideas work or fail.

## Learning target and utility remain separate

CatBoost minimizes target-before-stop log loss. It does not optimize a requested 70% win rate, one trade per day, any listed trade, or a backtest NAV directly. Those would encourage shortcut learning and make the model imitate the development sample rather than estimate the conditional event probability.

The calibrated probability is combined with each plan’s own post-cost win and loss R only at decision time. Positive expected log growth is the mathematical consequence of fixed 3% fractional risk, not a new discretionary score. The final continuous account still decides whether overlapping opportunities were actually tradable.

## Counterfactual sample policy

Candidate generation is widened only where an inherited broad-context quality gate used information that remains available as a feature. Structural geometry, first-touch lifecycle, target consumption and causal-episode ownership remain hard because relaxing them would change what the trade is.

Shadow mode reconstructs the inherited policy from causal factor snapshots so differences are attributable. Select mode evaluates all structurally complete plans. This provides losing and winning examples on both sides of the old gate, which is necessary to learn whether common flow was initiative, exhaustion or locally absorbed.

## Highest-value research loop

1. Complete a short real-data shadow run and inspect every emitted plan plus recurring terminal reasons.
2. Correct any mismatch between market episode and entry/stop/target before fitting a model.
3. Harvest disjoint periods spanning materially different auctions; preserve unresolved rows for censoring diagnostics but fit only resolved first passages.
4. Train once with chronological train/calibration/test separation and inspect calibration, feature dependence, family/symbol behavior and selected counterfactual geometry.
5. Freeze the model and run a later continuous four-symbol select account.
6. When results are weak, determine whether the recurring failure is candidate geometry, missing observable state, probability estimation or global arbitration. Change the responsible layer rather than adding generic thresholds.

The work is complete enough for long evaluation or paper only when the fixed integrated system—not a collection of isolated family summaries—shows strong cost-after-fee repeated expectancy, sufficient independent completed trades, resilient continuous NAV and no implementation ambiguity worth resolving first.
