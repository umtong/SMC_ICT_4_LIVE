# Direct Causal Auction Policy

This research path replaces the losing legacy plan generator rather than filtering it.
Every completed five-minute clock produces one symbol-neutral auction state from prior
price, aggressor flow, volatility, structure, cross-asset breadth and leader-lag state.
Long and short actions use only already-confirmed swing/volatility invalidations and
pre-entry targets.  Entry is the next one-minute open; first passage is conservative.

The action table is an offline full-information decision surface, not a live strategy.
A later robust policy must choose one contemporaneously available action or abstain and
then be evaluated as one global-position continuous account.
