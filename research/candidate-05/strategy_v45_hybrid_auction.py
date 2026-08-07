"""Candidate 05 v45: hierarchical multi-location, regime-aware auction router.

Four causally knowable liquidity sources compete without score fitting:
completed daily and eight-hour extremes, a completed low-efficiency twenty-
minute balance, and the mature confirmed five-minute pools.  The strongest
accessed source is consumed once.  v41 decides deleveraging rejection versus
position-building acceptance; v44 permits only the result consistent with the
completed sixty-minute price regime.  All order, risk, fee, slippage, position,
liquidation and NAV handling stays in NautilusTrader through the inherited v26
path.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from nautilus_trader.model.data import Bar

from strategy_base import _as_float
from strategy_v41_competing_auction import CompetingSweep, _finite
from strategy_v44_regime_auction import RegimeConditionedAuctionStrategy


NS_HOUR = 3_600_000_000_000
NS_DAY = 24 * NS_HOUR
NS_SESSION = 8 * NS_HOUR


@dataclass(slots=True)
class HybridLevel:
    level_id: str
    kind: str
    level: float
    source: str
    strength: float
    created_index: int
    completed_key: int
    consumed: bool = False


class HybridAuctionRouterStrategy(RegimeConditionedAuctionStrategy):
    BALANCE_BARS = 20
    BALANCE_MAX_RANGE_ATR = 2.0
    BALANCE_MAX_EFFICIENCY = 1.0 / 3.0
    BALANCE_BREAK_ATR = 0.10

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.v45_levels: list[HybridLevel] = []
        self.v45_session_key: int | None = None
        self.v45_day_key: int | None = None
        self.v45_session_high = -math.inf
        self.v45_session_low = math.inf
        self.v45_day_high = -math.inf
        self.v45_day_low = math.inf
        self.v45_balance_signatures: set[tuple[int, int, int]] = set()
        self.diagnostics.update(
            {
                "v45_external_levels": 0,
                "v45_balance_levels": 0,
                "v45_local_candidates": 0,
                "v45_external_candidates": 0,
                "v45_balance_candidates": 0,
                "v45_selected_completed_day": 0,
                "v45_selected_completed_8h": 0,
                "v45_selected_completed_balance": 0,
                "v45_selected_local_pool": 0,
            },
        )

    @staticmethod
    def _path_efficiency(rows: list[dict[str, float | int]]) -> float:
        if len(rows) < 2:
            return math.inf
        net = abs(float(rows[-1]["close"]) - float(rows[0]["open"]))
        path = sum(
            abs(float(rows[index]["close"]) - float(rows[index - 1]["close"]))
            for index in range(1, len(rows))
        )
        return net / path if path > 0.0 else 0.0

    def on_bar(self, bar: Bar) -> None:
        self._roll_external_levels(
            ts=int(bar.ts_event),
            high=_as_float(bar.high),
            low=_as_float(bar.low),
        )
        super().on_bar(bar)

    def _roll_external_levels(self, *, ts: int, high: float, low: float) -> None:
        session_key = ts // NS_SESSION
        day_key = ts // NS_DAY
        if self.v45_session_key is None:
            self.v45_session_key = session_key
        elif session_key != self.v45_session_key:
            if math.isfinite(self.v45_session_high) and math.isfinite(self.v45_session_low):
                key = int(self.v45_session_key)
                self.v45_levels.extend(
                    [
                        HybridLevel(f"8h-{key}-H", "HIGH", self.v45_session_high, "COMPLETED_8H", 3.0, self.bar_index, key),
                        HybridLevel(f"8h-{key}-L", "LOW", self.v45_session_low, "COMPLETED_8H", 3.0, self.bar_index, key),
                    ],
                )
                self.diagnostics["v45_external_levels"] += 2
            self.v45_session_key = session_key
            self.v45_session_high = -math.inf
            self.v45_session_low = math.inf
        if self.v45_day_key is None:
            self.v45_day_key = day_key
        elif day_key != self.v45_day_key:
            if math.isfinite(self.v45_day_high) and math.isfinite(self.v45_day_low):
                key = int(self.v45_day_key)
                self.v45_levels.extend(
                    [
                        HybridLevel(f"1d-{key}-H", "HIGH", self.v45_day_high, "COMPLETED_DAY", 4.0, self.bar_index, key),
                        HybridLevel(f"1d-{key}-L", "LOW", self.v45_day_low, "COMPLETED_DAY", 4.0, self.bar_index, key),
                    ],
                )
                self.diagnostics["v45_external_levels"] += 2
            self.v45_day_key = day_key
            self.v45_day_high = -math.inf
            self.v45_day_low = math.inf
        self.v45_session_high = max(self.v45_session_high, high)
        self.v45_session_low = min(self.v45_session_low, low)
        self.v45_day_high = max(self.v45_day_high, high)
        self.v45_day_low = min(self.v45_day_low, low)
        current_session = session_key
        self.v45_levels = [
            level for level in self.v45_levels
            if not level.consumed and (
                (level.source == "COMPLETED_DAY" and day_key - level.completed_key <= 3)
                or (level.source == "COMPLETED_8H" and current_session - level.completed_key <= 9)
            )
        ]

    def _balance_levels(self, atr: float) -> list[HybridLevel]:
        if len(self.bars) < self.BALANCE_BARS + 1:
            return []
        rows = list(self.bars)[-(self.BALANCE_BARS + 1):-1]
        high = max(float(row["high"]) for row in rows)
        low = min(float(row["low"]) for row in rows)
        if (high - low) / atr > self.BALANCE_MAX_RANGE_ATR:
            return []
        if self._path_efficiency(rows) > self.BALANCE_MAX_EFFICIENCY:
            return []
        signature = (
            int(rows[0]["ts"]),
            round(high / atr * 1_000_000),
            round(low / atr * 1_000_000),
        )
        if signature in self.v45_balance_signatures:
            return []
        key = int(rows[-1]["ts"])
        return [
            HybridLevel(f"bal-{signature[0]}-H", "HIGH", high, "COMPLETED_20M_BALANCE", 2.0, self.bar_index - self.BALANCE_BARS, key),
            HybridLevel(f"bal-{signature[0]}-L", "LOW", low, "COMPLETED_20M_BALANCE", 2.0, self.bar_index - self.BALANCE_BARS, key),
        ]

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        if self.v41_watch is not None:
            self._advance_competing_auction(row)
            return
        atr = _finite(self._atr())
        if not math.isfinite(atr) or atr <= 0.0:
            return

        candidates: list[tuple[HybridLevel, Any | None]] = []
        for level in self.v45_levels:
            candidates.append((level, None))
        for level in self._balance_levels(atr):
            candidates.append((level, None))
        for pool in self.active_pools.values():
            if self.bar_index - pool.created_index < self.config.pool_min_age_bars:
                continue
            candidates.append(
                (
                    HybridLevel(
                        str(pool.pool_id), str(pool.kind), float(pool.level),
                        "CONFIRMED_5M_POOL", 1.0, int(pool.created_index),
                        int(pool.created_index), False,
                    ),
                    pool,
                ),
            )

        high_crossed: list[tuple[HybridLevel, Any | None]] = []
        low_crossed: list[tuple[HybridLevel, Any | None]] = []
        for level, owner in candidates:
            if level.consumed:
                continue
            if (
                level.kind == "HIGH"
                and previous_close <= level.level
                and float(row["high"]) >= level.level + self.config.sweep_min_penetration_atr * atr
            ):
                high_crossed.append((level, owner))
            elif (
                level.kind == "LOW"
                and previous_close >= level.level
                and float(row["low"]) <= level.level - self.config.sweep_min_penetration_atr * atr
            ):
                low_crossed.append((level, owner))
        if high_crossed and low_crossed:
            for level, owner in high_crossed + low_crossed:
                self._consume_hybrid(level, owner, row, "V45_TWO_SIDED_ACCESS")
            self.diagnostics["v41_ambiguous_closed"] += 1
            return
        crossed = high_crossed or low_crossed
        if not crossed:
            return
        chosen_level, chosen_owner = max(
            crossed,
            key=lambda item: (
                item[0].strength,
                -abs(float(row["close"]) - item[0].level),
            ),
        )
        for level, owner in crossed:
            self._consume_hybrid(level, owner, row, "V45_LIQUIDITY_ACCESSED")
        if chosen_level.source == "COMPLETED_20M_BALANCE":
            prefix = chosen_level.level_id.rsplit('-', 1)[0]
            parts = chosen_level.level_id.split('-')
            try:
                start = int(parts[1])
            except (IndexError, ValueError):
                start = chosen_level.completed_key
            # Mark the complete balance, not only one side, as interpreted.
            self.v45_balance_signatures.add((start, 0, 0))
        pre = list(self.bars)[-(self.config.structure_lookback_bars + 1):-1]
        if not pre:
            return
        direction = 1 if chosen_level.kind == "HIGH" else -1
        self.scenario_counter += 1
        self.v41_watch = CompetingSweep(
            scenario_id=f"v45-{self.scenario_counter:07d}",
            pool_id=chosen_level.level_id,
            pool_kind=chosen_level.kind,
            pool_level=chosen_level.level,
            pool_source=chosen_level.source,
            pool_strength=chosen_level.strength,
            pool_created_index=chosen_level.created_index,
            sweep_direction=direction,
            sweep_index=self.bar_index,
            sweep_ts=int(row["ts"]),
            sweep_open=float(row["open"]),
            sweep_close=float(row["close"]),
            sweep_extreme=float(row["high"]) if direction > 0 else float(row["low"]),
            atr=atr,
            structure_high=max(float(item["high"]) for item in pre),
            structure_low=min(float(item["low"]) for item in pre),
            sweep_oi=self._fv("sum_open_interest"),
        )
        self.diagnostics["v41_sweeps_armed"] += 1
        source_key = {
            "COMPLETED_DAY": "v45_selected_completed_day",
            "COMPLETED_8H": "v45_selected_completed_8h",
            "COMPLETED_20M_BALANCE": "v45_selected_completed_balance",
            "CONFIRMED_5M_POOL": "v45_selected_local_pool",
        }[chosen_level.source]
        self.diagnostics[source_key] += 1

    def _consume_hybrid(self, level: HybridLevel, owner: Any | None, row: dict[str, float | int], reason: str) -> None:
        if owner is not None:
            self._consume_pool(owner, row, reason)
            self.diagnostics["v45_local_candidates"] += 1
        else:
            level.consumed = True
            if level.source == "COMPLETED_20M_BALANCE":
                self.diagnostics["v45_balance_candidates"] += 1
            else:
                self.diagnostics["v45_external_candidates"] += 1


CandidateStrategy = HybridAuctionRouterStrategy
StrategyClass = HybridAuctionRouterStrategy
