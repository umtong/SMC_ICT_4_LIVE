# Candidate 15 V3 failure evidence

V3 corrected a real execution-semantics error: every FAR stop had to remain at
or beyond the original sweep invalidation. The known V2 loss was explicitly
rejected as `C15_STOP_INSIDE_SWEEP_INVALIDATION`, while the known one-bar-fresh
winner remained.

The separately predeclared V1-V5 screen still failed:

- weekly-reset NAV multiple: `0.9699588353`
- daily geometric growth: `-0.0008714051586360434`
- closed trades: `1`
- wins / losses: `0 / 1`
- maximum interval drawdown: `0.030041164681`
- liquidation: none
- engine errors: none
- classification: `CANDIDATE15_V3_INSUFFICIENT_ACTIVITY`

The contaminated Candidate 13 reference replay retained only two of seven
previously published winning opportunities. Both retained trades won, but the
opportunity set was too sparse to solve the project objective.

## Sole unseen V3 loss

The only V1-V5 trade was XRPUSDT FAR short on 2026-02-12.

- state resolution was fresh on the entry bar;
- stop preserved the original sweep invalidation;
- entry and risk accounting were valid;
- XRP had already declined approximately 8.65% over the leadership event window,
  substantially more than the peer median;
- event path efficiency was only about 0.217;
- the semantic gate nevertheless approved additional downside as
  `SEMANTIC_FAR_MODERATE_COUNTERTREND_UNANIMOUS`.

The remaining failure was therefore not the local sweep classifier, state lease
or stop geometry. It was the upper cross-market role decision: the most depleted
market was treated as the preferred vehicle for further continuation.

## V4 decision

V4 does not relax V3 thresholds or add a symbol blacklist. It quarantines the
rejected SCDAM family and investigates an independent scenario family in which:

1. at least three markets first exhibit a common directional flow event;
2. a second distinct same-direction event activates a finite-lived initiative;
3. the periodic event itself is not traded;
4. a market must form a new post-activation five-minute structural leg;
5. opposite common flow, majority origin reacceptance or expiry terminates the
   state.

This tests whether repeated cross-market information, rather than a static
leader rank at one local sweep, can supply the missing higher-level router.
