# Candidate 10 v22 Clean Result

## Classification

`v22` is a **clean logic failure**. It solved v21's target-distance bottleneck but exposed a more important directional-state error.

Workflow run `31144644168`, job `92761468111`, commit `99b3dbe2c5bca3d9bd683ed616aee74e5860f3ef` completed source verification, all tests, full/ablation NautilusTrader runs and artifact upload without order or causality errors.

## Exact experiment

Full changed only the target hierarchy:

- source detector: unchanged v21
- OI state: required
- target: nearest directionally valid completed eight-hour `FUNDING_SESSION` pool

Exact ablation retained v21's nearest active pool, including five-minute confirmed pivots. Entry, stop, OI, fees, size-dependent impact, seed, first-later-TradeTick execution and 3% current-NAV planned-loss budget remained fixed.

## Results

| Metric | External session target | Nearest-any-pool ablation |
|---|---:|---:|
| plans / orders / trades | 8 / 8 / 8 | 0 / 0 / 0 |
| wins / losses | 1 / 7 | 0 / 0 |
| ending NAV | 82,999.8758 | 100,000.0000 |
| net return | -17.0001% | 0.0000% |
| geometric daily growth | -2.6268% | 0.0000% |
| impact-adjusted ending NAV | 80,289.8434 | 100,000.0000 |
| impact-adjusted net return | -19.7102% | 0.0000% |
| impact-adjusted geometric daily growth | -3.0874% | 0.0000% |
| impact-adjusted intraday drawdown | 19.8649% | 0.0000% |
| order errors | 0 | 0 |
| target pass | false | false |

The target hierarchy reduced cost-adjusted RR rejections from 41 to 4 and produced eight executable plans. It therefore worked as intended as an opportunity and reward-distance variable. The candidate still had strongly negative expectancy.

## Trade-level failure

All eight trades were `LEVERAGE_ACCEPTANCE_CONTINUATION` shorts after a sell-side boundary break.

- seven began with falling OI classified as `CLEARING`;
- one began with rising OI classified as `BUILDING`;
- seven exited at structural stops;
- one was approximately flat before fees but negative after conservative impact;
- no trade reached the external session target.

The seven clearing-state trades all lost after costs. Their initial move was a leveraged-position clearing event, but v22 interpreted boundary acceptance plus same-side flow as evidence to continue shorting. The external objective created enough nominal reward distance, yet the directional auction state was wrong.

The event log provides additional evidence:

- 14 clearing acceptance probes were invalidated when the next completed bar re-entered the old range;
- five building acceptance probes were also invalidated;
- only one building acceptance produced a trade, and it lost;
- no rejection scenario became executable.

## What worked

1. External-session targets repaired v21's cost/reward-distance failure.
2. OI remained useful relative to the no-OI v21 ablation.
3. Pre-existing pool identity and second-bar confirmation prevented v4's one-event multi-entry cascade.
4. Size-dependent impact and impact-adjusted NAV exposed that the only nominally positive trade was still economically negative.

## Primary failure cause

v22 used OI as an extreme-event filter but not as a directional state variable. It allowed both:

```text
OI decrease / position clearing → acceptance continuation
OI increase / new position building → acceptance continuation
```

These states are economically different. A price break driven by forced position clearing may exhaust the marginal flow and revert after liquidity is recovered. A break accompanied by new OI building is a more coherent continuation hypothesis.

## Decision

Discard v22 as a complete candidate. Preserve the external target hierarchy. Test one semantic change in v23:

```text
CLEARING + accepted break
→ do not enter continuation
→ wait for range reclaim and opposite executed flow
→ if confirmed, trade liquidation-exhaustion reversal

BUILDING + accepted break
→ retain continuation grammar
```

The exact ablation is v22's original mapping, where both clearing and building can continue. No threshold, target, risk, cost or timing parameter is changed.
