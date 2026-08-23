# Candidate 4t — sequential competing-hypothesis auction control

Candidate 4t is a research synthesis, not a threshold revision of an old policy.

It reuses the pieces that earlier branches already solved:

- semantic, pre-existing liquidity and one interaction owner from the liquidity-auction lineage;
- failed-auction reversal and accepted-auction continuation as two resolutions of one event;
- complete event-time episode states rather than a fixed arming window;
- OB/FVG/SR geometry only as a first-return price refinement;
- event invalidation for the stop and the first unconsumed opposing liquidity/causal-volume obstacle for the full-position target;
- no plan below 1.0 gross R;
- causal pending cancellation from candidate 2c;
- one continuous account slot, pending replacement by a better independent episode, immutable TP/SL after fill, and 3% NAV risk.

## The implemented gap

The code lineage contained all of those pieces, and candidate 2c's README described a sequential ownership belief, but no executable policy on this branch combined a real competing-hypothesis ownership model with execution geometry and continuation value. Earlier policies mostly estimated `TARGET_FIRST` independently for every action row. That lets entry geometry vote on direction and forces an artificial choice between the very early/high-frequency failure and the very late/rare failure already observed.

Candidate 4t implements the missing decision layer:

1. an **action-independent ownership model** sees price/volume, structure and auction response but not entry/stop/target geometry;
2. a **persistent episode filter** accumulates compatible evidence and resets stale belief on a contradictory control transfer;
3. separate fill and resolution models price the actual first-return instruction;
4. a learned **continuation value** compares entering now with waiting for the same causal episode;
5. the global router arbitrates BTC, ETH, SOL and XRP, and may replace only an unfilled order with a better independent opportunity.

There is no generic score threshold. An immutable plan arms only when its estimated post-cost log growth is positive and exceeds the estimated value of waiting. The models exclude symbol identity and absolute price/time fields. Regularized NumPy models and input SHA-256 hashes make the run reproducible in the pinned research image without a CatBoost runtime.

## Files

- `candidate_4t_harvest.py` — adapter over the 1k + complete-episode + 2c causal action stack.
- `candidate_4t_policy.py` — ownership, execution, continuation and one-account routing.
- `self_check.py` — sequential-belief and one-slot commitment invariants.

The short workflow publishes the actual selected orders, trades, losses and missed opportunities. Those separated weeks are development diagnostics, not a long-run claim.
