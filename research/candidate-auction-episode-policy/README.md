# Causal Auction Episode Policy Research

This candidate does not improve or filter an existing EasyChart strategy. It
reconstructs the decision itself.

A pre-existing 15m/60m wick boundary owns one independent interaction. The
engine observes whether the old auction fails (penetration, reclaim and local
control transfer) or transfers control (outside close, hold and response). Only
then does it emit complete actions. OB/FVG-style retracement prices appear as
conditional entries inside the causal event rather than unconditional candle
signals.

Every decision point enumerates market and post-only retest entries, the event's
natural invalidation, and pre-existing opposing objectives. Offline research
waits for an actual limit fill before labelling target-before-stop. A missed
entry remains unfilled; it is never rewritten as a profitable trade.

The research output is an action table, not a pass/fail report. Its purpose is to
discover which observable auction state, action and geometry can be reused by a
single cross-symbol policy before it is moved into NautilusTrader execution.
