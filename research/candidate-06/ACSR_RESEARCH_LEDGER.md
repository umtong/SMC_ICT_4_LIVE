# Candidate-06 ACSR research ledger

## Inherited evidence and explicit rejection boundaries

AFHR, HML and SIAR are not carried forward as complete candidates.

- HML produced +1.0243% geometric NAV/day on the first BTC week but collapsed to
  -4.7984%/day on sealed week 2 and -0.4132%/day on sealed week 3.
- AFHR's prior-only activity quality plus completed-close freshness reduced the
  sealed-week-2 loss to -0.1651%/day, but no ablation produced positive growth.
- SIAR flow surprise passed the first week at +1.6614%/day, then failed at
  -3.9662%/day and -0.4132%/day on the unchanged sealed weeks.
- The one-time SIAR week-2 attribution showed that impact efficiency, with or
  without the surprise threshold, reduced the known loss cluster to two trades
  and -0.0047%/day. It did not itself create positive expectancy.

Therefore ACSR does **not** add another continuation filter and does not tune the
SIAR quantile. Flow surprise is rejected as a directional edge. Impact
inefficiency is retained only as an event classifier.

## New structural claim

A completed accepted breakout with direction-aligned aggressive flow but weak
realized displacement is an absorption event, not an immediate fade signal.
The market must later reveal that inventory transfer has changed control:

1. a completed 30-minute auction first satisfies the inherited structural
   acceptance preconditions;
2. direction-aligned flow has sub-reference displacement-per-flow;
3. no position is opened on that auction;
4. a later completed 5-minute auction closes through the prior four completed
   5-minute auctions in the opposite direction with directional body, range and
   (when enabled) signed-flow agreement;
5. a completed 5-minute close beyond the original event extreme disproves the
   pending reversal;
6. only after opposite structure is confirmed does the inherited confirmed
   swing/equal-pool sweep and separate one-minute response become eligible.

The event bar is forbidden from self-confirming the reversal. All calculations
use completed auctions and prior-only reference distributions.

## Controlled variants

- `acsr_30m_full`: impact absorption plus independent opposite structure and
  structure-stage signed flow;
- `acsr_30m_structure_only_ablation`: remove only structure-stage signed flow;
- `acsr_30m_no_impact_ablation`: remove only impact classification while still
  requiring the later opposite structure break;
- `acsr_60m_full_horizon_reference`: identical full logic on the inherited 60m
  horizon, ineligible for selection.

The first eligible variant in the fixed order that passes the existing complete
first-week gate is locked unchanged for both sealed weeks. No session fitting,
direction-only switch, score multiplier, risk change, execution change or
threshold sweep is permitted.

## Terminal rule

If no 30-minute variant passes the first-week gate, the included single-variable
ablations are sufficient to distinguish over-selectivity from missing causal
information and ACSR is rejected without parameter polishing. If one passes the
first week but fails a sealed week, the frozen configuration is rejected and the
failure regime is recorded before any new candidate is opened.
