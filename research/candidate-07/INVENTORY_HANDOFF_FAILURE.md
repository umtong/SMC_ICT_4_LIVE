# Post-Liquidation Inventory Handoff — Failure Record

## Classification

`LOGIC_ERROR / ORIGINAL CANDIDATE DISCARDED`

An excursion-accounting implementation defect was found and corrected before
logic was judged: MFE and MAE had initially included bars after the first stop or
target.  The corrected run used identical signals, routes, stops, targets and
frozen data, with excursions truncated at the first terminal event.

## Original hypothesis

At the first causal contact with a confirmed 15-minute swing pool:

1. contact must contain aggressive flow and an OI-release impulse;
2. continued OI release plus an outside hold routes liquidation continuation;
3. release followed by opposite OI build plus reclaim routes liquidation
   exhaustion reversal;
4. stop and next unconsumed confirmed 15-minute target are fixed before path
   inspection.

## Exit-safe BTC Week-1 result

Period: `2025-12-22` through `2025-12-29` exclusive.

| Measure | Result |
|---|---:|
| Liquidation-release episodes | 28 |
| Entry-ready paths | 8 |
| Active days | 5 |
| Targets | 1 |
| Stops | 5 |
| Timeouts | 2 |
| Median pre-exit MFE | 0.7071 R |
| Median pre-exit MAE | 1.0465 R |

Route decomposition:

- `RELEASE_TO_BUILD_REVERSAL`: five entries, zero targets, three stops and two
  timeouts.  All five were lower-pool long reversals.
- `CONTINUED_RELEASE_ACCEPTANCE`: three entries, one target and two stops.

## Single controlled ablation

Removed exactly one core route:

`RELEASE_TO_BUILD_REVERSAL`

All source data, pool formation, thresholds, stops, targets, one-slot blocking
and exit-safe path accounting stayed unchanged.

| Measure | Baseline | Ablation |
|---|---:|---:|
| Entry-ready paths | 8 | 3 |
| Active days | 5 | 2 |
| Targets | 1 | 1 |
| Stops | 5 | 2 |
| Median MFE | 0.7071 R | 1.9875 R |
| Median MAE | 1.0465 R | 1.0850 R |

The ablation removed the weakest route but left insufficient density and more
stops than targets.  It does not provide a direct path to the project gate, so
the original candidate is discarded rather than tuned.

## Primary failure causes

1. **Instantaneous OI state was used as episode state.** Nineteen of 28 release
   episodes timed out because the following five-minute change was classified
   independently as neutral/build/release.  What matters is cumulative inventory
   pressure relative to the contact, not whether every individual bar is another
   rank-qualified release impulse.
2. **Continuation invalidation was too close to the broken pool.** One long
   continuation advanced `5.56 R` before its first stop event but had a distant
   `13.36 R` next-15-minute-pool target.  A shallow reclaim stop and a very remote
   target made normal mitigation fatal despite correct directional information.
3. **Only external 15-minute targets were available.** The strategy ignored
   nearer causal five-minute internal liquidity.  This repeated a previously
   identified target-hierarchy problem rather than a direction problem.
4. **Lower-pool long reversal remained structurally weak.** Requiring opposite
   OI build did not repair the persistent downside asymmetry seen in the earlier
   long evaluation.

## Components retained

- 15-minute pools confirmed only after two completed right-side bars;
- first-contact pool consumption;
- OI gaps invalidate state instead of being filled or interpolated;
- contact OI release and aggressor-flow concurrence;
- one pending/open route at a time;
- exit-safe path accounting and explicit post-stop diagnostics;
- continuation route showed meaningful favorable excursion in a subset of
  events and merits a redesigned, independent candidate.

## Next candidate—not a parameter tweak

The successor is an **episode inventory-pressure continuation** model:

- continuation only; no liquidation-reversal branch;
- cumulative OI relative to the contact represents continuing forced inventory;
- invalidation lies beyond the contact/confirmation auction structure, not just
  inside the broken pool;
- targets follow a causal internal-five-minute to external-fifteen-minute
  liquidity ladder;
- the same Week-1 density/expectancy diagnostic is rerun before any NautilusTrader
  execution strategy is built.

## Evidence

- Exit-safe workflow source: `3ffb27e50c3d970c6adfc120e3696bf4e9797b47`
- Exit-safe artifact SHA-256: `3d5724b364473c50e4b8ec6dfcc836fdf207e88eaeed75c9b6e7ec2355d82156`
- Ablation workflow source: `8505b18fdbb0a85fce41f79bf78550e43a404594`
- Ablation run: `31110606245`
- Ablation artifact SHA-256: `14dce66e3e9a7ec92a79751412024da6994b8bb83d080b3362efa0063272e889`
