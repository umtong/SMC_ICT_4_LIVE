"""Frozen public MBE2 source-recross invalidation candidate."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from strategy_mbe_lifecycle_observer_base import (
    Candidate35Config as _ObserverConfig,
    Candidate35Strategy as _ObserverStrategy,
)

_ALLOWED_HORIZONS = (15, 41, 114, 180, 420)


class Candidate35Config(_ObserverConfig, frozen=True):
    mbe_source_recross_enabled: bool = False
    mbe_source_recross_min_age_minutes: int = 0


class Candidate35Strategy(_ObserverStrategy):
    def __init__(self, config: Candidate35Config) -> None:
        if bool(config.mbe_source_recross_enabled):
            if int(config.mbe_source_recross_min_age_minutes) not in _ALLOWED_HORIZONS:
                raise ValueError(
                    "recross minimum age must be a supported public ROI horizon"
                )
        super().__init__(config)
        self.diagnostics.update(
            {
                "candidate57_mbe_source_recross_v1": 1,
                "mbe_source_recross_enabled": int(
                    bool(config.mbe_source_recross_enabled)
                ),
                "mbe_source_recross_min_age_minutes": int(
                    config.mbe_source_recross_min_age_minutes
                ),
                "mbe_source_recross_exit_requests": 0,
                "mbe_source_recross_policy_thresholds_searched": 0,
                "mbe_source_entry_changed": 0,
                "mbe_source_stop_changed": 0,
                "mbe_source_roi_changed": 0,
            }
        )

    @staticmethod
    def _recross_failed(snapshot: dict[str, Any]) -> bool:
        return (
            float(snapshot["estimated_after_cost_r"]) <= 0.0
            and float(snapshot["entry_symbol_rsi"]) >= 70.0
            and float(snapshot["entry_symbol_tema_slope_bps"]) > 0.0
        )

    def _manage_open_position(self, ts_event: int) -> None:
        scenario = self.current_scenario or {}
        family = str(scenario.get("scenario_family") or "")
        if (
            bool(self.config.mbe_source_recross_enabled)
            and family == "mbe"
            and self.current_symbol is not None
        ):
            moment = datetime.fromtimestamp(
                ts_event / 1_000_000_000,
                tz=timezone.utc,
            )
            age = max(0, self.minute_index - self.position_open_minute)
            if (
                moment.minute % 5 == 4
                and age >= int(self.config.mbe_source_recross_min_age_minutes)
            ):
                snapshot = self._snapshot(ts_event, age)
                if self._recross_failed(snapshot):
                    if self.current_scenario is not None:
                        self.current_scenario["mbe_source_recross_exit_snapshot"] = snapshot
                    self._close_family_position(
                        "PUBLIC_MBE2_SOURCE_RECROSS_INVALIDATION",
                        ts_event,
                        age_minutes=age,
                        minimum_age_minutes=int(
                            self.config.mbe_source_recross_min_age_minutes
                        ),
                        estimated_after_cost_r=float(
                            snapshot["estimated_after_cost_r"]
                        ),
                        entry_symbol_rsi=float(snapshot["entry_symbol_rsi"]),
                        entry_symbol_tema_slope_bps=float(
                            snapshot["entry_symbol_tema_slope_bps"]
                        ),
                        raw_short_cross_breadth=int(
                            snapshot["raw_short_cross_breadth"]
                        ),
                        renewed_short_pressure_breadth=int(
                            snapshot["renewed_short_pressure_breadth"]
                        ),
                    )
                    self.diagnostics["mbe_source_recross_exit_requests"] += 1
                    return
        super()._manage_open_position(ts_event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
