"""Candidate 09 v35: completed-auction liquidity plus footprint acceptance.

V33's footprint acceptance materially reduced losses, but every submitted trade
came from a two-bar confirmed swing. V35 changes only the context generator:
completed 15-minute, 60-minute and daily auction extremes replace local pivots.
The exact ablation re-enables confirmed swings while preserving every state,
entry, invalidation, target, cost, risk and execution rule.
"""
from __future__ import annotations

import math
from typing import Any

from strategy_v33 import Candidate16Config as _Candidate33Config
from strategy_v33 import Candidate16Strategy as _Candidate33Strategy

_MINUTE_NS = 60_000_000_000


class Candidate16Config(_Candidate33Config, frozen=True):
    candidate35_include_confirmed_swings: bool = False
    candidate35_enable_15m: bool = True
    candidate35_enable_60m: bool = True
    candidate35_enable_daily: bool = True


class Candidate16Strategy(_Candidate33Strategy):
    """V33 state/execution with completed-auction-only liquidity context."""

    def __init__(self, config: Candidate16Config) -> None:
        super().__init__(config=config)
        self._candidate35_auctions: dict[int, dict[str, float | int]] = {}
        self.diagnostics.update(
            {
                "candidate35_completed_15m": 0,
                "candidate35_completed_60m": 0,
                "candidate35_completed_daily": 0,
                "candidate35_confirmed_swings_enabled": int(
                    config.candidate35_include_confirmed_swings
                ),
            },
        )

    def _horizons(self) -> tuple[int, ...]:
        values: list[int] = []
        if self.config.candidate35_enable_15m:
            values.append(15)
        if self.config.candidate35_enable_60m:
            values.append(60)
        if self.config.candidate35_enable_daily:
            values.append(1440)
        return tuple(values)

    def _roll_session(self, row: dict[str, float | int]) -> None:
        """Freeze each completed auction at its final constituent bar."""
        ts_ns = int(row["ts"])
        # Bar visibility is open_time + one minute.  The represented minute is
        # therefore the minute ending at ts_ns, not the next minute.
        open_minute = ts_ns // _MINUTE_NS - 1
        for horizon in self._horizons():
            bucket = open_minute // horizon
            state = self._candidate35_auctions.get(horizon)
            if state is None or int(state["bucket"]) != bucket:
                state = {
                    "bucket": bucket,
                    "start_ts_ns": ts_ns - _MINUTE_NS,
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "bars": 1,
                }
                self._candidate35_auctions[horizon] = state
            else:
                state["high"] = max(float(state["high"]), float(row["high"]))
                state["low"] = min(float(state["low"]), float(row["low"]))
                state["bars"] = int(state["bars"]) + 1

            completed = (open_minute + 1) % horizon == 0
            if not completed or int(state["bars"]) < horizon:
                continue
            source = (
                "COMPLETED_DAILY"
                if horizon == 1440
                else f"COMPLETED_{horizon}M"
            )
            strength = 5 if horizon == 1440 else (3 if horizon == 60 else 2)
            self._add_pool(
                "HIGH",
                float(state["high"]),
                int(state["start_ts_ns"]),
                ts_ns,
                source,
                strength=strength,
            )
            self._add_pool(
                "LOW",
                float(state["low"]),
                int(state["start_ts_ns"]),
                ts_ns,
                source,
                strength=strength,
            )
            key = (
                "candidate35_completed_daily"
                if horizon == 1440
                else f"candidate35_completed_{horizon}m"
            )
            self.diagnostics[key] = int(self.diagnostics[key]) + 1
            self._candidate35_auctions.pop(horizon, None)

    def _confirm_pivots(self, row: dict[str, float | int]) -> None:
        if self.config.candidate35_include_confirmed_swings:
            super()._confirm_pivots(row)


__all__ = ["Candidate16Config", "Candidate16Strategy"]
