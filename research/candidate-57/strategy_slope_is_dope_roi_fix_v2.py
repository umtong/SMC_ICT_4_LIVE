"""Mechanical repair for the public Slope-is-Dope adapter ROI schedule.

The v1 adapter sorted Freqtrade's time-indexed ROI schedule in descending order,
while the reused execution helper expects ascending time.  That made the first
lookup at elapsed minute zero return the terminal zero-ROI value and caused
cost-only exits.  This wrapper changes no signal, stop, trailing or source-exit
logic; it only restores the declared ascending ROI schedule.
"""
from __future__ import annotations

from strategy_slope_is_dope_base import (
    Candidate35Config as Candidate35Config,
    Candidate35Strategy as _BuggyScheduleStrategy,
)


class Candidate35Strategy(_BuggyScheduleStrategy):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self._roi_schedule = tuple(
            sorted(
                (
                    (0, float(config.slope_roi_0)),
                    (
                        int(config.slope_roi_t1_minutes),
                        float(config.slope_roi_t1),
                    ),
                    (
                        int(config.slope_roi_t2_minutes),
                        float(config.slope_roi_t2),
                    ),
                    (
                        int(config.slope_roi_t3_minutes),
                        float(config.slope_roi_t3),
                    ),
                )
            )
        )
        self.diagnostics.update(
            {
                "candidate57_slope_roi_schedule_fix_v2": 1,
                "slope_roi_schedule_order": "ascending_elapsed_minutes",
                "slope_roi_schedule_v1_bug": "descending_order_returned_terminal_zero_at_entry",
            }
        )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
