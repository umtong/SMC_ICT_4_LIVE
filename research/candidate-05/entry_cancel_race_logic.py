"""Pure resolution states for a cancel-versus-fill entry race."""
from __future__ import annotations


def entry_cancel_resolution(
    *,
    cancel_requested: bool,
    cancel_confirmed: bool,
    position_open: bool,
    bar_index: int,
    requested_index: int,
) -> str:
    """Resolve an entry cancel without assuming event delivery order.

    NautilusTrader can match a resting order on a completed bar before the
    strategy receives that bar, while cancellation and position events are then
    delivered through the execution lifecycle. Scenario state therefore cannot
    be discarded at the cancel request itself.
    """
    if not cancel_requested:
        return "INACTIVE"
    if position_open:
        return "FLATTEN_FILLED_ENTRY"
    if cancel_confirmed and bar_index > requested_index:
        return "CLOSE_UNFILLED"
    return "WAIT"


__all__ = ["entry_cancel_resolution"]
