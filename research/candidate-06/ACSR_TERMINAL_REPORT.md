# Candidate-06 ACSR terminal report

## Execution integrity

The first workflow run exposed an implementation error before performance could
be interpreted: the absorption anchor and reversal bias used different
`scenario_id` values, while the event ledger correctly required one continuous
`ABSORPTION_ARMED -> BIAS_ACTIVE` state chain.  Only scenario identity was
repaired.  The BTC week, data, market rules, thresholds, execution assumptions,
risk and variant definitions were unchanged.  The same 2024-02-26 UTC week was
then replayed through NautilusTrader 1.230.0 and all pure causal tests plus the
unchanged parent-contract tests passed.

## Controlled first-week results

| Variant | Geometric NAV/day | Net PnL after cost | Trades | Wins | PF | MDD |
|---|---:|---:|---:|---:|---:|---:|
| `acsr_30m_full` | -2.1527% | -14,129.92 USDT | 5 | 0 | 0.000 | 14.13% |
| `acsr_30m_structure_only_ablation` | -2.1527% | -14,129.92 USDT | 5 | 0 | 0.000 | 14.13% |
| `acsr_30m_no_impact_ablation` | -2.1527% | -14,129.92 USDT | 5 | 0 | 0.000 | 14.13% |
| `acsr_60m_full_horizon_reference` | 0.0000% | 0.00 USDT | 0 | 0 | undefined | 0.00% |

The full 30-minute engine armed 16 impact-inefficient breakout anchors, observed
eight later opposite 5-minute structure breaks, armed eight downstream entries
and filled five.  Every filled trade stopped.  Removing only structure-stage
signed flow did not change a single filled trade or result.  Removing only the
impact classifier increased candidate-state activity but produced the same five
filled trades and the same outcomes.

## Logic diagnosis

This is a logic failure, not an execution or parameter failure.

- The event detector and causal ordering worked: the source auction never
  self-confirmed, later structure was required, directional extension disproved
  pending anchors, and state chains were complete.
- The useful SIAR component also remained real: impact inefficiency identified
  events and reduced continuation activity in the prior holdout.
- The invalid inference was the next step: a local opposite 5-minute close after
  absorption was usually a temporary inventory rebalance, not evidence that a
  durable opposite directional auction had taken control.
- Structure-flow and impact-classification ablations being execution-identical
  show that polishing those thresholds cannot repair the candidate.
- The 60-minute reference becoming inactive shows that merely enlarging the
  event horizon exchanges the same missing directional information for no
  opportunity.

Two trades briefly reached approximately +0.87R and +0.49R intrabar before
stopping, but the remaining entries had little or negative favorable excursion.
This does not support an exit tweak: the complete zero-win set and rapid accepted
boundary loss show that the directional context itself was unstable.

## Decision and retained learning

ACSR is rejected as a complete candidate.  No long evaluation is authorized.
Do not tune its structure lookback, body, flow, break buffer, confirmation age,
stop or target.

Retain only these findings:

1. impact inefficiency is an event classifier, not a standalone direction;
2. absorption plus one local CHoCH is insufficient for reversal;
3. independent completed-auction sequencing is implementable and auditable;
4. the next direction hypothesis must require persistent price impact across
   multiple completed auctions rather than infer reversal from failed impact.
