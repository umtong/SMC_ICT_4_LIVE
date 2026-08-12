"""State-dependent management adapter for the public MBE2 short entry.

The source entry, source arbitration and public ROI ladder are inherited
unchanged.  This wrapper changes only the maximum causal holding horizon after
entry, using the already-observed number of simultaneous source candidates:

* single-symbol episode: ``mbe_single_max_hold_minutes``;
* breadth episode (at least two symbols): ``mbe_breadth_max_hold_minutes``.

The state is fixed at submission time in ``mbe_collision_competitors``.  No
future price path or eventual outcome is used.
"""
from __future__ import annotations

import strategy_mbe2_base as _base


class Candidate35Config(_base.Candidate35Config, frozen=True):
    mbe_single_max_hold_minutes: int = 10_080
    mbe_breadth_max_hold_minutes: int = 10_080


class Candidate35Strategy(_base.Candidate35Strategy):
    def __init__(self, config: Candidate35Config) -> None:
        single_horizon = int(config.mbe_single_max_hold_minutes)
        breadth_horizon = int(config.mbe_breadth_max_hold_minutes)
        if single_horizon < 1 or breadth_horizon < 1:
            raise ValueError("MBE2 state horizons must be positive")
        super().__init__(config)
        self.diagnostics.update(
            {
                "candidate57_mbe_state_management": 1,
                "mbe_single_max_hold_minutes": single_horizon,
                "mbe_breadth_max_hold_minutes": breadth_horizon,
                "mbe_single_horizon_exits": 0,
                "mbe_breadth_horizon_exits": 0,
                "mbe_state_management_source_entry_changed": 0,
                "mbe_state_management_source_roi_changed": 0,
                "mbe_state_known_before_entry": 1,
            }
        )

    def _manage_open_position(self, ts_event: int) -> None:
        if self.current_symbol is not None and self.current_scenario is not None:
            if self.current_scenario.get("state") == _base.MBE_STATE:
                age = max(0, self.minute_index - self.position_open_minute)
                competitors = int(
                    self.current_scenario.get("mbe_collision_competitors", 0) or 0
                )
                breadth = competitors >= 1
                horizon = int(
                    self.config.mbe_breadth_max_hold_minutes
                    if breadth
                    else self.config.mbe_single_max_hold_minutes
                )
                if age >= horizon:
                    label = "BREADTH" if breadth else "SINGLE"
                    self._close_source_position(
                        f"CANDIDATE57_MBE_{label}_STATE_HORIZON",
                        ts_event,
                        age_minutes=age,
                        horizon_minutes=horizon,
                        actionable_candidates_at_entry=competitors + 1,
                        mbe_collision_competitors=competitors,
                    )
                    key = (
                        "mbe_breadth_horizon_exits"
                        if breadth
                        else "mbe_single_horizon_exits"
                    )
                    self.diagnostics[key] += 1
                    return
        super()._manage_open_position(ts_event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
