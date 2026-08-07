# UOAM Terminal Report

## Final classification

`LOGIC_FAILURE_NO_TRADEABLE_PREEXISTING_OBJECTIVE_PATH`

UOAM was executed through NautilusTrader on the frozen BTC week beginning
2024-02-26 after its payload/encoding implementation failure was repaired.  The
engine, data, timestamp and portfolio contracts completed without runtime
errors.  This is therefore not an implementation failure.

## Controlled evidence

The strict objective contract required an opposite-side swing/equal pool to be
confirmed before the accepted 60-minute auction.  It produced 18 accepted
contexts, only two bound objectives, two later counter-bias sweeps and no
completed response or trade.

A one-variable temporal ablation kept the pool source before the accepting
auction but allowed its right-side confirmation to complete by the auction end.
The result was identical: two objective bindings, sixteen contexts rejected for
no objective, zero trades and zero NAV change.  The strict regression matched
exactly.

The dynamic nearest-objective HML reference reproduced the known first-week
pass, so the absence of trades was caused by the pre-existing-objective thesis,
not by the shared HML detector, Nautilus execution, data or accounting.

## Largest performance factor

The requirement that a directional accepting impulse leave an already confirmed
opposite-side five-minute objective beyond its own extreme eliminated nearly all
contexts.  The two surviving objectives did not subsequently complete the
counter-bias sweep and separate response sequence.  Relaxing only confirmation
timing did not restore coverage.

## Valid component retained

An objective must be observable before entry and cannot be created after the
trade to explain a target.  Objective consumption and event-driven invalidation
remain useful system contracts.  However, an objective need not predate the
higher-timeframe impulse itself; that stronger claim is discarded.

## Decision

UOAM is closed.  No parameter, period, fee, risk, stop, target or frozen week is
changed to rescue it.  The next independent candidate replaces the single-venue
objective thesis with synchronized spot/perpetual price-discovery disagreement.
