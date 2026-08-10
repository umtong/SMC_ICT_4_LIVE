# 4h jump reversal — frozen fresh arbitration × boundary-handoff experiment

This experiment is frozen before reading its market result.  It is not a
binary promotion gate and it is not a threshold search.  Its purpose is to
separate two concrete causes discovered by the all-candidate causal audit:

1. simultaneous-symbol arbitration may select the most standardized-extreme
   member even when another qualifying member has more reversible auction
   space;
2. a newly completed independent 4-hour boundary is silently lost when the
   prior position reaches its source horizon on the same minute, because the
   one-slot strategy manages the old position and returns before routing the
   new boundary.

## Evaluation interval

- 2026-04-01 through 2026-04-14 UTC
- BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT
- one global pending entry or open position
- current-NAV planned-loss budget: 3%
- NautilusTrader matching, portfolio and account accounting
- project fees, adverse slippage and funding reserve

The interval is fresh to these two Candidate-57 policies.  It becomes
development data after this run and is not claimed to be globally untouched by
every prior project candidate.

## Signal, geometry and management held constant

All four cells preserve the same 4-hour specialist:

- current completed 4-hour log return compared with the prior 18 completed
  4-hour returns only;
- absolute causal z-score at least 2.0;
- enter opposite the completed impulse direction;
- structural stop beyond the completed 4-hour impulse extreme plus the frozen
  ATR/minimum buffer;
- 20% emergency target;
- original equal-time 240-minute horizon;
- transient protection armed at +0.4R and permanently escaped at +1.0R;
- no post-jump confirmation;
- one independent episode per symbol and completed 4-hour boundary.

No signal threshold, stop, target, protection parameter, cost or risk rule is
changed after seeing the interval.

## Factor A — simultaneous-symbol arbitration

### `source_max_z`

Preserve the existing source policy: among already-qualified simultaneous
candidates, choose the highest source score, which is dominated by the largest
absolute causal z-score.

### `least_qualifying_z`

The single pre-registered contrast from the prior all-candidate audit: once all
candidates already exceed the same 2-sigma abnormal-move threshold, choose the
candidate with the smallest absolute qualifying z-score.  The causal hypothesis
is that the most standardized-extreme member may retain more continuation risk,
whereas a less climactic qualifying peer may have more reversible space.

This policy is not asserted to be optimal.  No third arbitration rule will be
chosen from this interval.

## Factor B — source-horizon boundary handoff

### `no_handoff`

Preserve the existing control flow.  If a position is open, manage it and return.
A signal that completes exactly when the old position reaches its 240-minute
source horizon is not routed.

### `deferred_handoff`

At the exact source-horizon 4-hour boundary only:

1. observe and freeze the newly completed selected decision using information
   available at that boundary;
2. request the old position's normal source-horizon close;
3. submit the frozen new decision only on a later completed minute after the
   Nautilus account is actually flat;
4. never hold or submit two global positions;
5. expire the frozen decision after three completed minutes if flattening is
   delayed.

This changes control flow only.  It does not create a custom matching, portfolio
or account engine and does not permit overlap.

## Four frozen cells

1. `source_max_z__no_handoff`
2. `least_qualifying_z__no_handoff`
3. `source_max_z__deferred_handoff`
4. `least_qualifying_z__deferred_handoff`

All cells run sequentially on the same data with the same cache and configuration
except for the two explicit factors above.

## Required causal reading

The result will not be reduced to a PASS/FAIL label.  For every cell, preserve:

- every completed trade and its exit/management reason;
- every 4-hour source boundary and the selected symbol;
- the full simultaneous candidate set embedded in the decision diagnostics;
- every frozen, submitted and expired handoff event;
- account-slot occupation and subsequent reachable episodes;
- completed-trade R geometry, NAV path, drawdown and holding time;
- open/inflight orders and end-flat validity;
- whether a changed result came from arbitration, boundary handoff, altered
  account path, implementation behavior or ordinary probabilistic variation.

Raw symbol candidates at the same 4-hour boundary remain one causal market-event
family for independent-frequency analysis.  A better aggregate return is not
sufficient evidence unless the episode and code paths explain how it arose.

## Interpretation discipline

- If all cells are weak because the same market-event states continue rather
  than reverse, neither arbitration nor handoff is the missing solution.
- If handoff adds a trade, verify that it was a genuinely new completed
  boundary, entered only after flatness and not a duplicate symbol split.
- If an arbitration improves a selected trade but occupies the slot longer and
  blocks later value, evaluate the continuous account path rather than the
  pointwise trade outcome.
- If the interval contains no collisions or exact source-horizon handoff
  opportunities, record that the experiment was under-informative rather than
  declaring either factor valid.
- Any policy selected or modified after reading this interval must move to a
  different untouched interval before being treated as evidence.
