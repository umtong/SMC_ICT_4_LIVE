"""v37 state extension for confirmed internal-pivot risk ownership."""
from __future__ import annotations

from c10_v36_state import ConsequentEncroachmentRejectionEngine


class ConfirmedInternalPivotProtectionEngine(ConsequentEncroachmentRejectionEngine):
    """v36 detector/entry plus an explicit post-entry structure-protected state."""

    def mark_internal_pivot_protected(
        self,
        *,
        observed_ts_ns: int,
        pivot_event_ts_ns: int,
        direction: str,
        pivot_level: float,
        reference_extreme: float,
        protective_stop: float,
        original_stop: float,
    ) -> None:
        if self.active_trade_id is None:
            return
        previous_state = self.active_trade_state or "POSITION"
        reason = (
            "FIRST_POST_ENTRY_CONFIRMED_HIGHER_LOW"
            if direction == "LONG"
            else "FIRST_POST_ENTRY_CONFIRMED_LOWER_HIGH"
        )
        self._event(
            self.active_trade_id,
            "FAVORABLE_INTERNAL_PIVOT_CONFIRMED",
            pivot_event_ts_ns,
            observed_ts_ns,
            previous_state,
            "STRUCTURE_PROTECTED",
            reason,
            protective_stop,
            {
                "direction": direction,
                "pivot_level": pivot_level,
                "reference_extreme": reference_extreme,
                "protective_stop": protective_stop,
                "original_stop": original_stop,
                "risk_ownership_transition": (
                    "ORIGINAL_CE_RETEST_RISK_TO_CONFIRMED_INTERNAL_STRUCTURE"
                ),
            },
        )
        self.active_trade_state = "STRUCTURE_PROTECTED"


__all__ = ["ConfirmedInternalPivotProtectionEngine"]
