# Candidate 57 — ZaratustraV5 continuous-episode re-entry forensic v5

## Why this experiment exists

The all-candidate audit falsified the idea that the maximum source score is the
primary problem.  It also exposed a more basic accounting fact: 8,200 raw level
signals compressed to 1,125 independent continuous source episodes, while the
actual one-slot account completed 214 trades.  The public adapter marks every
completed five-minute level signal with its current timestamp, so a quick
trailing exit can be followed by another entry even when the underlying source
state never reset.

A repeated entry may be a legitimate new auction leg, or it may merely re-enter
an already consumed trend state.  Counting both as independent opportunities
without measuring their causal relationship would violate the project's trade-
independence requirement.

## Predeclared causal hypothesis

> The first account entry in a newly established continuous 5m/15m/30m source
> state captures most of the available continuation.  Re-entries submitted
> before that source state resets have less remaining favourable excursion and
> a larger probability of source stop or max-hold loss.

This is not a rule change.  The original one-slot account is replayed exactly and
each actual trade is tagged with the continuous source-episode ID and its entry
ordinal inside that episode.

## Episode definition

For each symbol independently, a source episode begins when the completed
five-minute decision changes from no actionable state or the opposite side into
a same-side actionable state.  It remains the same episode while consecutive
completed five-minute observations preserve that side.  A reset to no state or
opposite side ends it.

The state clock is updated even while another symbol occupies the global slot.
That permits the audit to distinguish:

- first account entry in a newly observed episode;
- first account entry after the episode began while the slot was blocked;
- second or later entry into the same episode after a prior account exit.

## Predicted observations

If the hypothesis is correct:

1. second-and-later entries should have lower mean R and PF than first entries;
2. re-entry MFE should fall and MAE should rise, not merely total PnL;
3. source-stop and max-hold shares should increase with entry ordinal;
4. the source state should already be materially older at re-entry;
5. the negative result should be distributed across many episodes, not one bad
   cascade;
6. the instrumented account must reproduce the frozen 214-trade June account
   exactly.

## Falsification

The hypothesis is rejected if re-entries are equal or superior in R, MFE/MAE
and exit mix, or if their losses are concentrated in a few events.  In that
case re-entry is a real renewal mechanism and must be preserved; independent
trade reporting will still collapse repeated trades to their shared causal
episode.

## Decision after the audit

Only a broad, repeated deterioration supports a minimal untouched experiment
that allows one entry per continuous state.  No source threshold, stop, target,
trailing or hold parameter will change.  If the hypothesis is false, the search
moves to market-state classification rather than suppressing repeated entries.
