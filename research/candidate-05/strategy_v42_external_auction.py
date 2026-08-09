"""Candidate 05 v42: completed external-session auction competition.

Only levels from a fully completed UTC day or eight-hour funding session are
eligible.  A level is known before use and is consumed on first material access.
Rejection and acceptance then compete exactly as in v41, while the mature v26
entry, cost, risk and NautilusTrader lifecycle remain unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any

from nautilus_trader.model.data import Bar

from strategy_base import _as_float
from strategy_v41_competing_auction import CompetingAuctionStrategy, CompetingSweep, _finite


NS_HOUR = 3_600_000_000_000
NS_DAY = 24 * NS_HOUR
NS_SESSION = 8 * NS_HOUR


@dataclass(slots=True)
class ExternalLevel:
    level_id: str
    kind: str
    level: float
    source: str
    completed_key: int
    created_index: int
    consumed: bool = False


class HierarchicalExternalAuctionStrategy(CompetingAuctionStrategy):
    """Trade one causal response to a completed external auction boundary."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.v42_levels: list[ExternalLevel] = []
        self.v42_session_key: int | None = None
        self.v42_day_key: int | None = None
        self.v42_session_high = -math.inf
        self.v42_session_low = math.inf
        self.v42_day_high = -math.inf
        self.v42_day_low = math.inf
        self.diagnostics.update(
            {
                "v42_session_levels_created": 0,
                "v42_daily_levels_created": 0,
                "v42_external_accesses": 0,
                "v42_levels_pruned": 0,
            },
        )

    def on_bar(self, bar: Bar) -> None:
        ts = int(bar.ts_event)
        high = _as_float(bar.high)
        low = _as_float(bar.low)
        self._roll_external_auctions(ts=ts, high=high, low=low)
        super().on_bar(bar)

    def _roll_external_auctions(self, *, ts: int, high: float, low: float) -> None:
        session_key = ts // NS_SESSION
        day_key = ts // NS_DAY
        if self.v42_session_key is None:
            self.v42_session_key = session_key
        elif session_key != self.v42_session_key:
            if math.isfinite(self.v42_session_high) and math.isfinite(self.v42_session_low):
                key = self.v42_session_key
                self.v42_levels.extend(
                    [
                        ExternalLevel(f"8h-{key}-H", "HIGH", self.v42_session_high, "COMPLETED_8H", key, self.bar_index),
                        ExternalLevel(f"8h-{key}-L", "LOW", self.v42_session_low, "COMPLETED_8H", key, self.bar_index),
                    ],
                )
                self.diagnostics["v42_session_levels_created"] += 2
            self.v42_session_key = session_key
            self.v42_session_high = -math.inf
            self.v42_session_low = math.inf
        if self.v42_day_key is None:
            self.v42_day_key = day_key
        elif day_key != self.v42_day_key:
            if math.isfinite(self.v42_day_high) and math.isfinite(self.v42_day_low):
                key = self.v42_day_key
                self.v42_levels.extend(
                    [
                        ExternalLevel(f"1d-{key}-H", "HIGH", self.v42_day_high, "COMPLETED_DAY", key, self.bar_index),
                        ExternalLevel(f"1d-{key}-L", "LOW", self.v42_day_low, "COMPLETED_DAY", key, self.bar_index),
                    ],
                )
                self.diagnostics["v42_daily_levels_created"] += 2
            self.v42_day_key = day_key
            self.v42_day_high = -math.inf
            self.v42_day_low = math.inf
        self.v42_session_high = max(self.v42_session_high, high)
        self.v42_session_low = min(self.v42_session_low, low)
        self.v42_day_high = max(self.v42_day_high, high)
        self.v42_day_low = min(self.v42_day_low, low)
        cutoff = session_key - 9  # three completed days, strictly intraday context
        before = len(self.v42_levels)
        self.v42_levels = [
            level for level in self.v42_levels
            if not level.consumed and (
                level.source == "COMPLETED_DAY"
                or level.completed_key >= cutoff
            )
        ]
        self.diagnostics["v42_levels_pruned"] += before - len(self.v42_levels)

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        if self.v41_watch is not None:
            self._advance_competing_auction(row)
            return
        atr = _finite(self._atr())
        if not math.isfinite(atr) or atr <= 0.0:
            return
        high_crossed = [
            level for level in self.v42_levels
            if not level.consumed
            and level.kind == "HIGH"
            and previous_close <= level.level
            and float(row["high"]) >= level.level + self.config.sweep_min_penetration_atr * atr
        ]
        low_crossed = [
            level for level in self.v42_levels
            if not level.consumed
            and level.kind == "LOW"
            and previous_close >= level.level
            and float(row["low"]) <= level.level - self.config.sweep_min_penetration_atr * atr
        ]
        if high_crossed and low_crossed:
            for level in high_crossed + low_crossed:
                level.consumed = True
            self.diagnostics["v41_ambiguous_closed"] += 1
            return
        crossed = high_crossed or low_crossed
        if not crossed:
            return
        if high_crossed:
            chosen = max(high_crossed, key=lambda level: (level.level, level.source == "COMPLETED_DAY"))
            kind, direction = "HIGH", 1
        else:
            chosen = min(low_crossed, key=lambda level: (level.level, level.source != "COMPLETED_DAY"))
            kind, direction = "LOW", -1
        for level in crossed:
            level.consumed = True
        pre = list(self.bars)[-(self.config.structure_lookback_bars + 1):-1]
        if not pre:
            return
        self.scenario_counter += 1
        self.v41_watch = CompetingSweep(
            scenario_id=f"v42-{self.scenario_counter:07d}",
            pool_id=chosen.level_id,
            pool_kind=kind,
            pool_level=chosen.level,
            pool_source=chosen.source,
            pool_strength=2.0 if chosen.source == "COMPLETED_DAY" else 1.0,
            pool_created_index=chosen.created_index,
            sweep_direction=direction,
            sweep_index=self.bar_index,
            sweep_ts=int(row["ts"]),
            sweep_open=float(row["open"]),
            sweep_close=float(row["close"]),
            sweep_extreme=float(row["high"]) if kind == "HIGH" else float(row["low"]),
            atr=atr,
            structure_high=max(float(item["high"]) for item in pre),
            structure_low=min(float(item["low"]) for item in pre),
            sweep_oi=self._fv("sum_open_interest"),
        )
        self.diagnostics["v41_sweeps_armed"] += 1
        self.diagnostics["v42_external_accesses"] += 1


CandidateStrategy = HierarchicalExternalAuctionStrategy
StrategyClass = HierarchicalExternalAuctionStrategy
