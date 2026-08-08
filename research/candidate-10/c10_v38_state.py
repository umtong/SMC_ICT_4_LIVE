"""v38 one-minute right-confirmed microstructure state."""
from __future__ import annotations

from typing import Any

from logic import BarObs, TradePlan

from c10_v37_state import ConfirmedInternalPivotProtectionEngine


class ConfirmedMicroPivotProtectionEngine(
    ConfirmedInternalPivotProtectionEngine,
):
    """v37 state machine plus causal one-minute swing confirmation."""

    def __init__(self, config: Any, instrument_id: str) -> None:
        super().__init__(config, instrument_id)
        self.micro_highs: list[tuple[int, int, float]] = []
        self.micro_lows: list[tuple[int, int, float]] = []

    def _confirm_micro_pivot(self, observed_ts_ns: int) -> None:
        if len(self.bars) < 3:
            return
        left, center, right = self.bars[-3:]
        if (
            center.high > left.high
            and center.high > right.high
        ):
            self.micro_highs.append(
                (center.ts_ns, observed_ts_ns, center.high),
            )
        if (
            center.low < left.low
            and center.low < right.low
        ):
            self.micro_lows.append(
                (center.ts_ns, observed_ts_ns, center.low),
            )

    def on_bar(
        self,
        bar: BarObs,
        *,
        allow_entry: bool = True,
    ) -> TradePlan | None:
        plan = super().on_bar(bar, allow_entry=allow_entry)
        self._confirm_micro_pivot(bar.ts_ns)
        return plan

    def mark_micro_pivot_protected(
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
            "FIRST_POST_ENTRY_CONFIRMED_ONE_MINUTE_HIGHER_LOW"
            if direction == "LONG"
            else "FIRST_POST_ENTRY_CONFIRMED_ONE_MINUTE_LOWER_HIGH"
        )
        self._event(
            self.active_trade_id,
            "FAVORABLE_MICRO_PIVOT_CONFIRMED",
            pivot_event_ts_ns,
            observed_ts_ns,
            previous_state,
            "MICRO_STRUCTURE_PROTECTED",
            reason,
            protective_stop,
            {
                "direction": direction,
                "pivot_timeframe": "ONE_MINUTE",
                "pivot_level": pivot_level,
                "reference_level": reference_extreme,
                "protective_stop": protective_stop,
                "original_stop": original_stop,
                "risk_ownership_transition": (
                    "ORIGINAL_CE_RETEST_RISK_TO_CONFIRMED_ONE_MINUTE_STRUCTURE"
                ),
            },
        )
        self.active_trade_state = "MICRO_STRUCTURE_PROTECTED"


__all__ = ["ConfirmedMicroPivotProtectionEngine"]
