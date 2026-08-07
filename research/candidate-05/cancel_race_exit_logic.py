"""Pure lifecycle decision for a fill which races an entry-cancel request."""
from __future__ import annotations

NO_ACTION = "NO_ACTION"
WAIT_FOR_CONTINGENT_RESOLUTION = "WAIT_FOR_CONTINGENT_RESOLUTION"
SUBMIT_MARKET_FLATTEN = "SUBMIT_MARKET_FLATTEN"


def cancel_race_exit_action(
    *,
    position_open: bool,
    open_reduce_only_orders: bool,
    flatten_submitted: bool,
) -> str:
    """Choose one mutually exclusive exit action."""
    if not position_open or flatten_submitted:
        return NO_ACTION
    if open_reduce_only_orders:
        return WAIT_FOR_CONTINGENT_RESOLUTION
    return SUBMIT_MARKET_FLATTEN


__all__ = [
    "NO_ACTION",
    "SUBMIT_MARKET_FLATTEN",
    "WAIT_FOR_CONTINGENT_RESOLUTION",
    "cancel_race_exit_action",
]
