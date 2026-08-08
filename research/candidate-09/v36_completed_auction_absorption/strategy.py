"""Candidate 09 v36: completed-auction context plus extreme absorption reversal.

V34 separated absorption, opposite initiative and pullback but still used local
confirmed swings. V36 changes only liquidity selection to completed 15-minute,
60-minute and daily auction extremes. The exact ablation re-enables confirmed
swings while preserving all later state, entry, invalidation, target, cost and
risk rules.
"""
from __future__ import annotations

from strategy_v34 import Candidate16Config as _Candidate34Config
from strategy_v34 import Candidate16Strategy as _Candidate34Strategy

_MINUTE_NS = 60_000_000_000


class Candidate16Config(_Candidate34Config, frozen=True):
    candidate36_include_confirmed_swings: bool = False
    candidate36_enable_15m: bool = True
    candidate36_enable_60m: bool = True
    candidate36_enable_daily: bool = True


class Candidate16Strategy(_Candidate34Strategy):
    def __init__(self, config: Candidate16Config) -> None:
        super().__init__(config=config)
        self._candidate36_auctions: dict[int, dict[str, float | int]] = {}
        self.diagnostics.update(
            {
                "candidate36_completed_15m": 0,
                "candidate36_completed_60m": 0,
                "candidate36_completed_daily": 0,
                "candidate36_confirmed_swings_enabled": int(
                    config.candidate36_include_confirmed_swings
                ),
            }
        )

    def _horizons(self) -> tuple[int, ...]:
        values: list[int] = []
        if self.config.candidate36_enable_15m:
            values.append(15)
        if self.config.candidate36_enable_60m:
            values.append(60)
        if self.config.candidate36_enable_daily:
            values.append(1440)
        return tuple(values)

    def _roll_session(self, row: dict[str, float | int]) -> None:
        ts_ns = int(row["ts"])
        open_minute = ts_ns // _MINUTE_NS - 1
        for horizon in self._horizons():
            bucket = open_minute // horizon
            state = self._candidate36_auctions.get(horizon)
            if state is None or int(state["bucket"]) != bucket:
                state = {
                    "bucket": bucket,
                    "start_ts_ns": ts_ns - _MINUTE_NS,
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "bars": 1,
                }
                self._candidate36_auctions[horizon] = state
            else:
                state["high"] = max(float(state["high"]), float(row["high"]))
                state["low"] = min(float(state["low"]), float(row["low"]))
                state["bars"] = int(state["bars"]) + 1
            if (open_minute + 1) % horizon != 0 or int(state["bars"]) < horizon:
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
                "candidate36_completed_daily"
                if horizon == 1440
                else f"candidate36_completed_{horizon}m"
            )
            self.diagnostics[key] = int(self.diagnostics[key]) + 1
            self._candidate36_auctions.pop(horizon, None)

    def _confirm_pivots(self, row: dict[str, float | int]) -> None:
        if self.config.candidate36_include_confirmed_swings:
            super()._confirm_pivots(row)


__all__ = ["Candidate16Config", "Candidate16Strategy"]
