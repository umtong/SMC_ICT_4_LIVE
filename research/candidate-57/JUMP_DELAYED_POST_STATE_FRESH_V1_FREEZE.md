# Candidate 57 — frozen delayed post-cascade jump-state fresh experiment

## Structural reason for changing the entry state

Immediate four-hour jump reversal has now failed on more than one untouched
2026 interval.  The most recent July factor map was negative in all active
cells, including peer-taker aligned and short-only variants.  Continuing to
change the jump z threshold, stop buffer or symbol arbitration would therefore
be threshold rescue of an unstable entry state.

An external liquidation-reversal implementation and preregistered failure
analysis provide a different solution: do not buy or sell merely because a
large cascade has completed.  Wait for the cascade to stop extending and for
leveraged positioning to stop deteriorating.  The project already contains a
reusable causal confirmation state which waits for a completed five-minute
close through the terminal jump candle in the reversal direction and builds the
stop around any post-jump extension.

This experiment composes those existing solutions.  It changes the state
transition before entry, not the source jump detector, risk, target or
management.

## Fresh interval

- scored entry interval: **2026-07-29 through 2026-08-09 UTC**;
- Binance USD-M metrics sidecar begins 2026-07-25 for strict as-of joins;
- no result from this interval was read before the three cells were frozen;
- all cells use the same four-symbol data and one continuous account slot;
- the interval becomes development data immediately after the first result is
  observed.

## Three frozen cells

| cell | entry transition after source four-hour jump |
|---|---|
| `immediate_control` | enter immediately using peer-taker conditional arbitration |
| `two_bar_price_confirmation` | wait at least two completed five-minute bars; enter only after a close through the terminal jump-candle extreme in the reversal direction |
| `two_bar_price_oi_stable` | same price confirmation, and target-contract open interest may not be more than 1% below its strict-as-of source-boundary value |

Delayed cells expire after 15 completed minutes.  A confirmation may first be
accepted after ten completed minutes, so it cannot be a one-bar reaction.

## Price confirmation

For a proposed long reversal after a downward jump:

- wait at least ten completed minutes;
- the latest completed five-minute close must be above the terminal jump-minute
  high;
- any lower post-jump extension is included in structural invalidation.

For a proposed short reversal after an upward jump, reverse the inequalities.
The entry and stop are rebuilt from information available at the confirmation
boundary.  The source event's 240-minute clock is not restarted.

## Open-interest stabilization

For `two_bar_price_oi_stable` only:

- select the latest Binance metrics row at or before the source event boundary;
- select the latest row at or before the confirmation boundary;
- reject future rows and rows older than ten minutes;
- require `current_sum_open_interest / source_sum_open_interest - 1 >= -0.01`;
- use the target contract only; peer OI and the eventual price path are not
  consulted.

The 1% tolerance is imported from the external post-liquidation stabilization
rule.  It is not fitted on project outcomes.

## Frozen source, routing and execution contract

Unchanged in all cells:

- completed four-hour return with absolute prior-only z-score at least 2.0;
- 18 prior completed four-hour returns for volatility;
- initial proposed side opposite the completed impulse;
- peer-taker conditional one-slot arbitration:
  - at least three of four peers aligned -> source maximum absolute z;
  - otherwise -> least absolute already-qualified z;
- both reversal directions remain eligible;
- whole-event source horizon of 240 minutes;
- transient protection armed at +0.4R and escaped at +1.0R;
- no symbol-specific rule;
- BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT;
- one global pending entry or open position;
- current-NAV 3% planned-loss sizing;
- realistic fees, adverse slippage, funding safety and NautilusTrader matching;
- simultaneous symbols at one source boundary count as one causal event.

## Interpretation

The authoritative result is the actual continuous one-slot account.  Shadow
candidate paths are diagnostic only.

A delayed state is useful only if it improves cost-after expectancy and NAV
path without reducing independent opportunities to a one-event anecdote.  The
OI condition is promoted only if it adds information beyond price confirmation;
a cleaner result caused only by rejecting nearly everything is not enough.

If both delayed cells fail, immediate four-hour jump reversal is no longer a
priority alpha family.  Preserve its reusable cascade detector and external
state loaders, but move research effort to MBE2 or a structurally different
scenario rather than adding more jump thresholds.
