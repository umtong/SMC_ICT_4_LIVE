# Candidate 01 v21 — Failed Sweep → MSS → Broken-Pivot Retest

## Frozen question

Does restoring the missing SMC/ICT market-structure-shift transition improve the
cost-after failed-sweep scenario?

The frozen event order was:

1. a cost-resolved 40-bps intrinsic directional change confirms a same-side
   liquidity sweep failure and reversal of aggregate-trade flow;
2. a completed equal-notional event closes through the nearest confirmed
   opposing pivot with aligned flow (MSS/CHoCH);
3. a later completed event retests that broken pivot and closes rejected with
   aligned flow;
4. entry occurs on the first venue TradeTick after confirmation;
5. invalidation is beyond the post-MSS retest-path extreme plus the unchanged
   7-bps buffer;
6. the farther of the two latest opposing 40-bps pivots is the target.

No PnL-fitted expiry, retracement ratio or risk multiplier was added.

## Authoritative first unused BTC week

- Frozen before execution: `2023-10-02T00:00:00Z` to
  `2023-10-09T00:00:00Z`
- Engine: NautilusTrader `1.230.0`
- Execution data: official Binance Vision USD-M aggregate trades represented
  one-for-one as NautilusTrader TradeTicks
- Costs: 7 bps per side
- Planned risk: current Nautilus account NAV × 3%
- Maximum hold: four hours
- Custom fill/PnL/NAV simulator: none

### v21 MSS-retest

| Diagnostic | Result |
|---|---:|
| failed-sweep setups armed | 52 |
| MSS confirmations | 21 |
| broken-pivot reacceptances | 15 |
| retest-rejection confirmations | 3 |
| evaluation-period plans | 2 |
| Nautilus submissions | 0 |
| closed positions | 0 |
| cost-dominated rejections | 2 |
| total return | 0.00% |
| geometric daily return | 0.00% |

Both evaluation plans were short:

| Entry | Stop | Target | Price-risk share | Cost-after RR | Result |
|---:|---:|---:|---:|---:|---|
| 27,240.2 | 27,276.5 | 27,220.0 | 0.4875 | -0.2407 | cost dominated |
| 27,397.3 | 27,423.2 | 27,337.2 | 0.4030 | 0.3390 | cost dominated |

The state machine generated ample failed sweeps and a meaningful number of MSS
transitions. The failure occurred after that: by the time the broken-pivot
retest was confirmed, the farther of the two local 40-bps opposing pivots was
already too close to support the fixed execution-cost contract.

### Identical-stream legacy boundary-retest control

| Diagnostic | Result |
|---|---:|
| failed-sweep setups armed | 53 |
| boundary-retest confirmations | 19 |
| evaluation-period plans | 13 |
| Nautilus submissions | 1 |
| closed positions | 1 |
| win rate | 0.00% |
| total return | **-3.0032%** |
| geometric daily return | **-0.4347%** |
| maximum drawdown | **-3.0032%** |

The only executable legacy trade entered long near `27,636.3`, used a structural
stop near `27,490.7`, targeted `27,960.9`, and closed at its stop. The v21 MSS
state machine invalidated this setup before entry because the failed-sweep
boundary was reaccepted before the required internal-pivot break.

## Interpretation

The result is not simply “MSS works” or “MSS fails.”

- The MSS transition removed the only authoritative legacy losing trade in this
  week, so it contributed useful causal information.
- The local 40-bps directional-change pivots were suitable for detecting and
  confirming structure, but not for defining the final external-liquidity
  destination after an additional MSS and retest delay.
- Tightening the local stop further would reproduce the v20 cost-dominance
  failure. Entering before MSS would reproduce the legacy false entry.

The unresolved variable is therefore target hierarchy, not another signal
threshold or stop parameter. The next candidate must preserve the v21 trigger
and invalidation while sourcing the destination from a genuinely external,
causally available liquidity layer.

## Decision

`STOP` — do not open the second and third frozen weeks for v21. Preserve the MSS
state transition and replace only the target layer in the next independent
candidate.
