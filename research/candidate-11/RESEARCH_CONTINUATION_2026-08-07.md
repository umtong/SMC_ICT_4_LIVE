# Candidate 11 continuation protocol

This ledger records the next research cycle without treating signal diagnostics as filled-trade performance.

## Mandatory order

1. Run `smc4 doctor` in the pinned environment.
2. Compile and test causality, exact 3% NAV risk sizing, and the global one-position invariant.
3. Execute the unchanged frozen BTC W1 interval in NautilusTrader.
4. Require non-empty run, data-manifest, orders, positions, account, events, plans, and metrics evidence.
5. Repair implementation failures on W1 only.
6. Treat a normally executed but weak result as a logic/frequency failure.
7. Unlock W2 and W3 only when W1's promising gate passes.

Structural target-first labels are diagnostics only. They are never reported as win rate, filled PnL, or NAV growth.

## Failure classification

- Missing evidence, timestamp faults, engine exceptions, order rejection, overlapping positions, budget mismatch, or liquidation: implementation/evidence failure.
- Submitted plans without closed Nautilus positions: execution-policy failure.
- No executable plans after a valid run: scenario/frequency failure.
- Closed trades with weak after-cost growth: logic/expectancy failure.
- W1 promising gate passed: freeze logic and continue to independent W2.

## Causal model retained

SCDAM distinguishes failed-auction reversal from accepted-auction continuation around completed source-session ranges. FAR tracks the final raid extreme; AAC freezes the pre-pullback impulse extreme. The two hypotheses never share invalidation state. Entries use the first causal retracement and require positive costed structural room to previously confirmed external liquidity.

## Next extension

If BTC W1 has positive after-cost expectancy but insufficient independent opportunities, apply the unchanged state machine to ETH, SOL, and XRP on the same W1 and arbitrate all four markets through one global entry/position mutex. Do not relax thresholds merely to manufacture frequency.
