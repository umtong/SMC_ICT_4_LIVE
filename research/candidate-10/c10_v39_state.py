"""v39 entry-auction acceptance state extension."""
from __future__ import annotations

from c10_v38_state import ConfirmedMicroPivotProtectionEngine


class EntryAuctionAcceptanceEngine(ConfirmedMicroPivotProtectionEngine):
    """v38 scenario plus post-fill ownership of the passive entry boundary."""

    def mark_entry_auction_evaluated(
        self,
        *,
        fill_ts_ns: int,
        observed_ts_ns: int,
        direction: str,
        boundary: float,
        completed_close: float,
        accepted: bool,
        distance_from_boundary: float,
    ) -> None:
        if self.active_trade_id is None:
            return
        previous_state = self.active_trade_state or "POSITION"
        next_state = (
            "ENTRY_AUCTION_ACCEPTED"
            if accepted
            else "ENTRY_AUCTION_FAILED_EXIT_PENDING"
        )
        reason = (
            "COMPLETED_FILL_BAR_HELD_PREDICTED_SIDE_OF_PASSIVE_BOUNDARY"
            if accepted
            else "COMPLETED_FILL_BAR_FAILED_TO_HOLD_PASSIVE_BOUNDARY"
        )
        self._event(
            self.active_trade_id,
            (
                "ENTRY_AUCTION_ACCEPTANCE_CONFIRMED"
                if accepted
                else "ENTRY_AUCTION_HOLD_FAILED"
            ),
            fill_ts_ns,
            observed_ts_ns,
            previous_state,
            next_state,
            reason,
            boundary,
            {
                "direction": direction,
                "entry_boundary": boundary,
                "completed_close": completed_close,
                "distance_from_boundary": distance_from_boundary,
                "evaluation_contract": (
                    "first completed minute containing or following real fill; "
                    "zero-buffer close-side acceptance"
                ),
            },
        )
        self.active_trade_state = next_state


__all__ = ["EntryAuctionAcceptanceEngine"]
