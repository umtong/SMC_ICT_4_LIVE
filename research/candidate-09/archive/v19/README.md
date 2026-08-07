# Candidate-09 v19 — discarded role-reversal retest

## Decision

**Discarded as the primary entry family after an implementation-clean frozen-week run.**

The baseline required every accepted-breakout failure to revisit the failed boundary from
inside and reject before entry. It produced only two trades across the fixed 21-day screen,
with +0.0957% cost-after daily geometric growth, two active weeks, and 100% of positive PnL
from one trade. The exact v18 control remained much stronger on the short screen, while the
`direct-only` role-reversal control was cost-after negative.

## Failure mechanism

- 322 failed-boundary role-reversal setups were armed.
- 148 reaccepted outside instead of confirming the reversal.
- 85 reached the source equilibrium before a retest entry.
- 129 expired without a qualified retest/rejection.
- 43 produced geometry or cost-after reward/risk that was not tradeable.
- Only two entries were approved.

A later retest can diagnose boundary role reversal, but it is usually too late to serve as
the execution event for this target geometry. Requiring it only on the defended-retest path
preserved a positive short-screen result, but that control retained the direct `ACCEPTED`
failure path already shown to destroy the account over the fixed three-year evaluation.

## Preserved findings

1. Loss of outside acceptance is not evidence that opposite liquidity has formed.
2. A boundary retest is useful as a state diagnostic, not automatically as an entry trigger.
3. The accepted-range-extreme failure family has no remaining structural repair path under
   the current one-minute aggregate observations without accumulating fitted conditions.
4. Future candidates should form an independent auction state before the probe, rather than
   infer durable value from a single failed extreme.

## Reproducibility

- Workflow run: `31156904936`
- Trigger: `e464724a2052c098ef56f1693083fb488060b152`
- Result commit: `dcda9a68f9a56141e02e30e090fa55dc1efd9019`
- Doctor, compile, contracts, NautilusTrader gate and account reconciliation all completed.
- Full source/evidence artifact: `candidate-09-v19-31156904936`
