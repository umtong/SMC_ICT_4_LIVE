# Candidate 10 v23 Result — Clean Logic Failure and Lineage Discard

## Reproduction evidence

- Branch: `research/candidate-10`
- Executed source commit: `c7c07f6f8eaa77bf0b95ee03475c19454646e916`
- GitHub Actions workflow: `candidate-10-v23-research`
- Run: `31158122817`
- Job: `92802065409`
- Artifact: `8986128561`
- Artifact digest: `sha256:830d94f9943a3502938eb32b40a8095986bba451ca3043fc07edf02937d108cd`
- BTC evaluation week: `2023-10-16` through `2023-10-22` UTC
- Engine: pinned NautilusTrader `1.230.0`
- Risk: 3% of current whole-account all-cost NAV

The automatic phase stopped after the first BTC week because the full variant
did not pass. The other two preselected weeks were not unlocked.

## Implementation and evidence gate

The run completed normally.

- `smc4 doctor`: Python `3.13.5`, NautilusTrader `1.230.0`, glibc `2.36` all OK.
- Hash-verified v21 base source materialized successfully.
- All source files compiled.
- 23 regression tests passed:
  - four original liquidation-state tests;
  - three fixed-point impact tests;
  - five external-target tests;
  - seven live all-cost ledger tests;
  - four v23 OI-semantic routing tests.
- Data gaps: 0.
- Duplicate aggregate-trade IDs: 0.
- Nonmonotonic aggregate-trade timestamps: 0.
- Causality violations: 0.
- Order errors: 0 for full and ablation.
- Open positions at termination: 0.

The new conservative ledger also reconciled exactly.

| Variant | Risk-budget violations | Max ledger/budget error | Final NAV reconciliation error |
|---|---:|---:|---:|
| Full OI-semantic routing | 0 | 0.0 | 0.0 |
| v22-mapping ablation | 0 | `4.55e-13` | 0.0 |

Modeled impact was debited at actual entry and exit fill timestamps and every
later quantity used Nautilus whole-account equity minus all prior modeled cost.
Wins and concentration were computed from impact-adjusted trade PnL.

## Exact controlled comparison

The only causal difference was the meaning assigned to an accepted break with
falling OI.

```text
Full:
CLEARING acceptance
→ continuation prohibited
→ old-range reclaim + opposite executed flow required
→ reversal

Ablation:
CLEARING acceptance
→ v22 continuation retained
```

`BUILDING` acceptance, detector, pools, expiry, entry, stop, external session
target, fees, fill-time impact ledger, seed and 3% risk were identical.

## Performance

| Metric | Full: clearing-reclaim reversal | Ablation: v22 clearing continuation |
|---|---:|---:|
| Closed trades | 2 | 8 |
| Cost-after wins / losses | 0 / 2 | 0 / 8 |
| Raw-engine wins / losses | 0 / 2 | 1 / 7 |
| Raw ending NAV | 94,742.3944 | 83,188.2015 |
| Conservative ending NAV | 94,077.6101 | 80,512.6244 |
| Conservative net return | -5.9224% | -19.4874% |
| Conservative geometric daily growth | -0.8684% | -3.0491% |
| Conservative intraday maximum drawdown | 5.9224% | 19.5104% |
| Positive / negative / flat days | 0 / 2 / 5 | 0 / 5 / 2 |
| Modeled impact | 664.7842 | 2,675.5772 |
| Target pass | false | false |

The exact ablation confirms that the v22 continuation mapping was destructive:
with corrected all-cost sizing it produced eight trades and all eight were
negative after impact. Its only raw-engine winner was still a cost-after loser.

The full mapping avoided most of those bad trades and reduced drawdown, but it
did not create positive expectancy. Both executed full scenarios reached their
structural stop:

| Scenario | Direction | Cost-adjusted planned RR | Holding time | Cost-after PnL |
|---|---:|---:|---:|---:|
| `LEVERAGE_ACCEPTANCE_CONTINUATION` (`BUILDING`) | short | 5.6335 | 1h 26m 56s | -3,006.4723 |
| `LIQUIDATION_CLEARING_EXHAUSTION_REVERSAL` | short | 3.1050 | 4h 35m 52s | -2,915.9176 |

No external session target was reached.

## State diagnosis

The full state machine created 53 `CLEARING_ACCEPTANCE_RECLAIM_WAIT` episodes.

- 39 expired without both range reclaim and opposite flow;
- 11 reclaimed but had no cost-qualified external target;
- 3 confirmed a clearing-exhaustion reversal;
- one confirmed reversal occurred inside the no-entry end-of-day window;
- one occurred outside the evaluation interval;
- one was executed and stopped.

The single in-period `BUILDING` continuation was also executed and stopped.

This establishes two separate failures.

1. **Frequency:** only 3 of 53 clearing waits confirmed the required auction
   transition, and only one produced an executable in-period trade.
2. **Expectancy:** the executed clearing reversal and building continuation were
   both wrong directionally before the distant target became relevant.

The full variant's improvement over the ablation came from abstaining from seven
bad continuation trades, not from a repeatable positive trade state.

## Classification

**Clean logic failure.**

This is not an implementation, evidence, cost-ledger, risk-sizing or order
lifecycle failure. The OI-semantic split is a useful defensive classifier, but
it is not a sufficient alpha source. Falling OI plus a short reclaim and flow
flip does not reliably distinguish temporary leverage clearing from renewed
price discovery. Rising OI plus same-side acceptance also failed in the one
clean opportunity.

## Decision

The candidate-10 liquidation-auction lineage is discarded as a complete trading
system. No further generation may rescue it by changing OI quantiles, reclaim
distance, confirmation bars, stop buffer, target hierarchy, time exclusions,
fees, risk, or the selected week.

Retained infrastructure and learning:

- immutable source/data provenance;
- one-source-event-one-scenario identity;
- first-later-TradeTick entry timing;
- raw-tick stop/target observation;
- external-versus-internal liquidity hierarchy;
- fixed-point size-dependent impact;
- fill-time all-cost NAV ledger;
- OI clearing/building as a state descriptor, not a directional command.

The next generation must introduce a genuinely new causal object with
independent information about who is leading price formation and whether
liquidity replenishes. The selected path is spot–perpetual auction
reconciliation, using completed cross-market price and executed-flow states,
with NautilusTrader still owning all futures execution and accounting.
