# Session-Handoff Liquidity Auction — Failure Record

## Classification

`LOGIC_ERROR / DISCARDED`

The implementation compiled, all causal/data tests passed, checksum-verified
public data loaded, and the same frozen BTC Week-1 interval reproduced
identically.  The failure is therefore not an execution or timestamp defect.

## Hypothesis

Three completed UTC session ranges were treated as public dealing ranges.  The
first later contact with a completed range boundary was classified using
completed aggressor flow and open-interest state:

- OI release plus aggressive rejection -> liquidation reversal;
- OI build plus aggressive rejection -> trapped-inventory reversal;
- OI build plus outside displacement and hold -> new-inventory continuation.

The pattern detector and trading scenario were kept separate.  No orders or
counterfactual NAV were produced at this diagnostic stage.

## Frozen BTC Week-1 baseline

Period: `2025-12-22` through `2025-12-29` exclusive.

| Measure | Result |
|---|---:|
| Classified scenarios | 7 |
| Entry-ready paths | 3 |
| Structural targets reached | 0 |
| Stops reached | 2 |
| Timeouts | 1 |
| Median MFE | 0.6264 R |
| Median MAE | 1.2486 R |

The three entry-ready paths were:

1. Europe-to-US new-inventory short continuation: stop, `1.06 R` MFE and
   `8.51 R` MAE over the diagnostic horizon.
2. Asia-to-Europe liquidation long reversal: stop, `0.31 R` MFE.
3. Asia-to-Europe trapped-inventory short reversal: timeout, `0.63 R` MFE.

## Single controlled ablation

Removed exactly one core route:

`NEW_INVENTORY_ACCEPTANCE_CONTINUATION`

All data, period, session boundaries, thresholds, target construction and path
accounting remained unchanged.

| Measure | Baseline | Ablation |
|---|---:|---:|
| Entry-ready paths | 3 | 2 |
| Structural targets reached | 0 | 0 |
| Stops reached | 2 | 1 |
| Timeouts | 1 | 1 |
| Median MFE | 0.6264 R | 0.4678 R |
| Median MAE | 1.2486 R | 0.8783 R |

The ablation removed one bad continuation, but did not create positive reversal
expectancy.  Opportunity density fell and favorable excursion also deteriorated.
This is not a structural route to the project target.

## Primary failure cause

A completed six-hour session boundary is a real and public liquidity reference,
but boundary contact plus contemporaneous OI/flow does not identify *when forced
inventory is exhausted*.  The model faded or followed the first qualified
contact without observing the subsequent inventory handoff.  It therefore
confused:

- liquidation that is still propagating with liquidation exhaustion;
- fresh inventory that is accepted with temporary outside marking;
- a public reference level with an executable invalidation point.

The result was adverse path asymmetry: favorable movement was generally below
one initial risk unit, while adverse movement reached or exceeded the structural
stop.

## Components retained

The following parts behaved as intended and remain reusable:

- session ranges were fully completed before becoming liquidity pools;
- each boundary was consumed on first causal contact;
- aggressor flow and OI were completed observations, not look-ahead features;
- invalid/non-positive OI snapshots broke state rather than being synthesized;
- structural midpoint/opposite-boundary/extension targets were declared before
  path evaluation;
- baseline and ablation used the same frozen interval and data manifests.

## Design implication

The next hypothesis must model the *post-contact inventory transition*, not merely
classify inventory at contact.  A reversal should require forced-inventory
release followed by opposite-side inventory build and price/flow displacement.
Continued release without that handoff should not be faded.

## Evidence

- Workflow source commit: `b848d54185eabaec61a1514cc89165978e8c299b`
- Workflow run: `31109440372`
- Artifact: `candidate-07-session-handoff-ablation-b848d54185eabaec61a1514cc89165978e8c299b`
- Artifact SHA-256: `bc5dfa1b544941067351d9e7b005937faa37e16c34dacd8cd5a779e57cc544f5`
