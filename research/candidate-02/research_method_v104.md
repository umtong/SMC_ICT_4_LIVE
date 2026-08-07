# Candidate-02 research-method update through v104

## Decision hierarchy learned from prior candidates

1. **Classify infrastructure and implementation faults before judging alpha.** A workflow which never reaches market data or NautilusTrader contains no alpha evidence. Conversely, v103 reached NautilusTrader successfully; its negative NAV is logic/performance evidence and may not be relabeled as plumbing failure. A causal-index, activation, or window bug is fixed under the same locked week and scenario only when found before that candidate's performance is observed.
2. **Reject a scenario for logical insufficiency even when it trades frequently.** v103 emitted 19 central trades but failed on win rate, PF, daily growth and drawdown. Activity density is not a substitute for pre-existing liquidity, invalidation and a natural target.
3. **Do not confuse correct local direction with a valid target.** Prior candidates often achieved favorable excursion but failed at synthetic or skipped distant objectives. MFE is diagnostic; only realized cost-after NAV is performance.
4. **Separate reusable evidence from failed packages.** Common spot/perpetual acceptance, limited basis-only expansion, causal displacement/FVG and retest defense survived as components. Flow persistence, short opposite flow and fixed clock events did not survive as standalone direction rules.
5. **One causal question per ablation.** The v104 ablation removes one approximate liquidity family; it does not loosen several confirmations or retune thresholds after seeing PnL.
6. **Use the nearest natural liquidity target.** A candidate which needs to skip the nearest target to reach a favorable RR has contradicted its own market narrative.
7. **Delay execution beyond the confirmation close and repeat all entry-quality checks.** Signal confirmation and order activation are separate states. The execution layer must use the actual activation price, reject a bar which already crossed stop or target, recheck target activity, delivery fraction and cost-after RR, and only then size the trade.
8. **Keep session and regime labels diagnostic until causal evidence exists.** They may explain failures later, but they are not filters added to rescue the first week.

## Error taxonomy

### Implementation error

Examples: wrong dataframe endpoint, incomplete higher-timeframe bar admitted, timestamp unit mismatch, wrong column name, target incorrectly considered intact, workflow not registered, exact SHA not checked out. Response: fix with variable control, rerun identical locked data and scenario.

### Logic error

Examples: event detector does not identify the pursued liquidity, target has no market-structural source, confirmation contradicts prior evidence, opportunity density is structurally insufficient, nearest target is uneconomic. Response: one precommitted ablation; if no structural path remains, reject and record useful components.

### Performance failure

A fully executed, correctly implemented scenario can still fail its cost-after NAV gates. Performance failure alone does not identify the cause. Diagnostics must determine whether direction, timing, target, invalidation, cost or regime concentration is responsible before the next hypothesis.

## v104 prospective rule

No v104 market data may be collected before the lock file contains the first week, code/config Git blob hashes, fixed ablation and validation contract. No second/third week or long evaluation may run unless the central baseline passes prospectively. The single ablation is explanatory only and cannot retroactively promote the family.

## v103 evidence incorporated before v104 data

The completed v103 first-week artifact is now the source of truth: central u8 PF 0.4271, daily growth -3.3358%, MDD -25.65%; u10 PF 0.8449, daily growth -0.6246%; the sole refill-ceiling ablation worsened drawdown. The next hypothesis therefore changes market logic rather than threshold tuning: external-liquidity registry, old-range invalidation, common-market acceptance, post-acceptance displacement/retest, and nearest natural target.

## Activation implementation error found before v104 collection

Static review found that the original v104 signal builder tested RR and delivery only at the decision close, while the inherited adapter sized at the later activation close. That could admit an economically degraded entry. It also did not reject an activation bar which had already crossed stop or target. This was classified as an implementation error because no v104 market data or performance had been observed. The week, hypothesis, parameters, risk, costs, target rule and fixed ablation were unchanged; pure activation tests were added before lock hashes were regenerated.

## Pre-data implementation audit added before v104 collection

The prospective lock was audited again after v103 evidence was recovered and before any v104 archive was collected. The audit separated five implementation defects from scenario logic: the acceptance close could be reused as a supposedly post-acceptance displacement; a target confirmed only on the activation bar could be selected with future information; the activation bar could cross the old-range invalidation without touching a wider stop; executable tick rounding occurred after risk sizing; and reused data/execution dependencies were not all blob-locked. The week, scenario hypothesis, thresholds, risk, costs and single ablation were unchanged. Synthetic state and adapter tests were expanded before market data access.
