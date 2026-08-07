# Candidate 10 v21 Clean Result

## Classification

`v21` is a **clean logic failure**, not an implementation failure and not a successful zero-risk system.

The complete source contract, `smc4 doctor`, compilation, unit tests, causal event validation, Binance manifests, NautilusTrader execution, exact no-OI ablation, order lifecycle and artifact upload all passed in workflow run `31143754500`, job `92758888496`, commit `87e9a5b3321608377a84359d69b822948df8eb32`.

## Fixed evaluation contract

- BTC week: `2023-10-16`
- full: OI state required
- exact ablation: remove only OI state
- detector, pools, second-bar confirmation, first-later-TradeTick entry, stop, target, fees, size-dependent impact, seed and 3% current-NAV planned-loss budget fixed
- execution/accounting: NautilusTrader 1.230.0

## Full result

| Metric | Value |
|---|---:|
| completed 5m observations | 4,031 |
| pools created | 1,009 |
| active retained pools | 76 |
| acceptance probes | 70 |
| rejection probes | 7 |
| OI-state rejections | 22 |
| cost-adjusted target rejections | 41 |
| plans / orders / trades | 0 / 0 / 0 |
| ending NAV | 100,000.00 USDT |
| impact-adjusted ending NAV | 100,000.00 USDT |
| geometric daily growth | 0.0000% |
| order errors | 0 |
| target pass | false |

Zero trades are not treated as success. The full candidate did not provide sufficient executable opportunity.

## Exact no-OI ablation

| Metric | Value |
|---|---:|
| plans / orders / trades | 1 / 1 / 1 |
| wins / losses | 0 / 1 |
| ending NAV | 97,262.9844 USDT |
| net return | -2.7370% |
| geometric daily growth | -0.3957% |
| impact-adjusted ending NAV | 96,994.6931 USDT |
| impact-adjusted net return | -3.0053% |
| impact-adjusted geometric daily growth | -0.4350% |
| impact-adjusted intraday drawdown | 3.3382% |
| order errors | 0 |

The one no-OI trade was a long acceptance continuation from a confirmed high pivot at 29,825.0. It entered at 29,949.8997, stopped at 29,760.37028 and targeted another confirmed pivot at 30,720.0. OI change was ordinary (`-0.00549%`). The trade lost `2,737.02` USDT before the separate conservative impact debit and `3,005.31` USDT after it.

## What worked

1. The OI requirement removed the only clean executable losing trade admitted by the exact ablation. OI therefore had useful selection value in this week.
2. Pre-existing pool identity prevented v4's repeated re-entry into one rapid event.
3. A second completed 5m bar and first-later-TradeTick entry preserved causal sequencing.
4. Size-dependent impact and conservative NAV reporting preserved the 3% planned-loss contract without an arbitrary nominal cap.
5. No future-time, order-lifecycle or accounting error contaminated this result.

## Primary failure cause

The machine generated many structurally valid price/flow/OI confirmations, but most failed executable cost-adjusted reward because `_target_pool()` selected the nearest active pool of the required side. The retained pool set was dominated by five-minute confirmed pivots:

- confirmed-pivot pools created: 1,003
- completed eight-hour funding-session pools created: 6
- cost-adjusted target rejections: 41 in full, 53 in no-OI

The system therefore used the same micro structural scale for both the event trigger and the profit objective. The resulting target was usually too close to pay entry/exit fees, stop risk and size-dependent impact.

This is a source-target hierarchy failure, not evidence that the threshold values should be tuned. Adding filters, changing quantiles or reducing risk would not create structural reward distance.

## Decision

Discard v21 as a complete candidate. Preserve OI, pre-existing pool identity, second-bar confirmation, first-later-tick execution and impact-aware NAV. Test one structural change in v22: internal five-minute pivots may trigger a scenario, but only completed eight-hour funding-session extremes may serve as external profit targets.
