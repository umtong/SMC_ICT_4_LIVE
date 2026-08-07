"""Candidate 05 v41: competing rejection/acceptance auction after a sweep.

Unlike a late entry filter, v41 replaces the initial directional commitment.
After one past-known pool is consumed, rejection and acceptance are observed in
parallel.  The first fully coherent state is handed to the mature v26
confirmation, entry, cost, risk and NautilusTrader execution path.  If neither
state completes, the pool remains consumed and no trade is manufactured.
"""
from __future__ import annotations

from dataclasses import dataclass
import inspect
import math
from typing import Any

import strategy_v26 as _v26
from strategy_base import PendingSetup
try:
    from strategy_v6 import PositionBuildingSetup
except ImportError:  # pragma: no cover - fixed repository always contains v6
    PositionBuildingSetup = None  # type: ignore[assignment]


def _base_class() -> type:
    found = [
        value for value in vars(_v26).values()
        if isinstance(value, type)
        and value.__module__ == _v26.__name__
        and value.__name__.endswith("Strategy")
    ]
    if len(found) != 1:
        raise RuntimeError(f"expected one v26 strategy, found {[x.__name__ for x in found]}")
    return found[0]


_BASE = _base_class()


def _finite(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def _construct(cls: type, **values: Any) -> Any:
    signature = inspect.signature(cls)
    accepted = {
        name: value
        for name, value in values.items()
        if name in signature.parameters
    }
    missing = [
        name for name, parameter in signature.parameters.items()
        if name != "self"
        and parameter.default is inspect.Parameter.empty
        and name not in accepted
    ]
    if missing:
        raise RuntimeError(f"cannot construct {cls.__name__}; missing {missing}")
    return cls(**accepted)


@dataclass(slots=True)
class CompetingSweep:
    scenario_id: str
    pool_id: str
    pool_kind: str
    pool_level: float
    pool_source: str
    pool_strength: float
    pool_created_index: int
    sweep_direction: int
    sweep_index: int
    sweep_ts: int
    sweep_open: float
    sweep_close: float
    sweep_extreme: float
    atr: float
    structure_high: float
    structure_low: float
    sweep_oi: float
    rejection_holds: int = 0
    acceptance_holds: int = 0


class CompetingAuctionStrategy(_BASE):
    """Choose rejection or acceptance before arming an inherited entry path."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.v41_watch: CompetingSweep | None = None
        self.diagnostics.update(
            {
                "v41_sweeps_armed": 0,
                "v41_rejection_selected": 0,
                "v41_acceptance_selected": 0,
                "v41_ambiguous_closed": 0,
                "v41_expired": 0,
                "v41_missing_positioning": 0,
            },
        )

    def _fv(self, name: str) -> float:
        try:
            return _finite(self._feature(name))
        except Exception:
            return math.nan

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        if self.v41_watch is not None:
            self._advance_competing_auction(row)
            return
        atr = _finite(self._atr())
        if not math.isfinite(atr) or atr <= 0.0:
            return
        high_crossed = [
            pool for pool in self.active_pools.values()
            if pool.kind == "HIGH"
            and self.bar_index - pool.created_index >= self.config.pool_min_age_bars
            and previous_close <= pool.level
            and float(row["high"]) >= pool.level + self.config.sweep_min_penetration_atr * atr
        ]
        low_crossed = [
            pool for pool in self.active_pools.values()
            if pool.kind == "LOW"
            and self.bar_index - pool.created_index >= self.config.pool_min_age_bars
            and previous_close >= pool.level
            and float(row["low"]) <= pool.level - self.config.sweep_min_penetration_atr * atr
        ]
        if high_crossed and low_crossed:
            for pool in high_crossed + low_crossed:
                self._consume_pool(pool, row, "V41_AMBIGUOUS_TWO_SIDED_SWEEP")
            self.diagnostics["v41_ambiguous_closed"] += 1
            return
        crossed = high_crossed or low_crossed
        if not crossed:
            return
        if high_crossed:
            pool = max(high_crossed, key=lambda item: (item.level, item.strength))
            kind, direction = "HIGH", 1
        else:
            pool = min(low_crossed, key=lambda item: (item.level, -item.strength))
            kind, direction = "LOW", -1
        for item in crossed:
            self._consume_pool(item, row, "V41_LIQUIDITY_ACCESSED")
        self.diagnostics["accessed_pools"] += len(crossed)
        self.scenario_counter += 1
        pre = list(self.bars)[-(self.config.structure_lookback_bars + 1):-1]
        if not pre:
            return
        scenario_id = f"v41-{self.scenario_counter:07d}"
        self.v41_watch = CompetingSweep(
            scenario_id=scenario_id,
            pool_id=pool.pool_id,
            pool_kind=kind,
            pool_level=float(pool.level),
            pool_source=str(pool.source),
            pool_strength=float(pool.strength),
            pool_created_index=int(pool.created_index),
            sweep_direction=direction,
            sweep_index=int(self.bar_index),
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

    def _advance_competing_auction(self, row: dict[str, float | int]) -> None:
        watch = self.v41_watch
        if watch is None:
            return
        age = self.bar_index - watch.sweep_index
        if age <= 0:
            return
        if age > int(self.config.rejection_confirmation_bars):
            self.v41_watch = None
            self.diagnostics["v41_expired"] += 1
            return

        close = float(row["close"])
        flow15 = self._fv("flow_15s")
        flow60 = self._fv("flow_60s")
        flow3m = self._fv("flow_3m")
        depth = self._fv("depth_imbalance_1")
        efficiency = self._fv("efficiency_60s")
        burst = self._fv("notional_burst")
        oi = self._fv("sum_open_interest")
        if not all(math.isfinite(value) for value in (flow15, flow60, depth, efficiency, burst)):
            return
        oi_change = (
            oi / watch.sweep_oi - 1.0
            if math.isfinite(oi) and math.isfinite(watch.sweep_oi) and watch.sweep_oi > 0.0
            else math.nan
        )
        if not math.isfinite(oi_change):
            self.diagnostics["v41_missing_positioning"] += 1
            return

        rejection_side = -watch.sweep_direction
        acceptance_side = watch.sweep_direction
        reclaimed = (
            close < watch.pool_level if watch.pool_kind == "HIGH"
            else close > watch.pool_level
        )
        outside_distance = acceptance_side * (close - watch.pool_level) / watch.atr
        rejection_progress = rejection_side * (close - watch.sweep_close) / watch.atr

        rejection_now = (
            reclaimed
            and oi_change <= 0.0
            and rejection_side * flow15 > 0.0
            and rejection_side * flow15 > rejection_side * flow60
            and rejection_side * depth > 0.0
            and rejection_progress >= 0.05
            and efficiency >= 0.10
            and burst >= 1.0
        )
        acceptance_now = (
            outside_distance >= float(self.config.acceptance_close_atr)
            and oi_change > 0.0
            and acceptance_side * flow15 > 0.0
            and acceptance_side * flow60 > 0.0
            and math.isfinite(flow3m)
            and acceptance_side * flow3m >= 0.0
            and acceptance_side * depth > 0.0
            and efficiency >= float(self.config.acceptance_efficiency_min)
            and burst >= 1.0
        )
        watch.rejection_holds = watch.rejection_holds + 1 if rejection_now else 0
        watch.acceptance_holds = watch.acceptance_holds + 1 if acceptance_now else 0

        rejection_ready = watch.rejection_holds >= 1
        acceptance_ready = watch.acceptance_holds >= int(self.config.acceptance_min_hold_bars)
        if rejection_ready and acceptance_ready:
            self.v41_watch = None
            self.diagnostics["v41_ambiguous_closed"] += 1
            return
        if rejection_ready:
            self._arm_rejection(watch, row, oi_change)
            return
        if acceptance_ready:
            self._arm_acceptance(watch, row, oi_change)

    def _details(self, watch: CompetingSweep, oi_change: float) -> dict[str, Any]:
        return {
            "pool_id": watch.pool_id,
            "pool_kind": watch.pool_kind,
            "pool_level": watch.pool_level,
            "pool_source": watch.pool_source,
            "pool_strength": watch.pool_strength,
            "pool_age_minutes": watch.sweep_index - watch.pool_created_index,
            "sum_open_interest": watch.sweep_oi,
            "oi_change_sweep_to_selection": oi_change,
            "flow_15s": self._fv("flow_15s"),
            "flow_60s": self._fv("flow_60s"),
            "flow_3m": self._fv("flow_3m"),
            "efficiency_60s": self._fv("efficiency_60s"),
            "notional_burst": self._fv("notional_burst"),
            "depth_imbalance_1": self._fv("depth_imbalance_1"),
        }

    def _arm_rejection(self, watch: CompetingSweep, row: dict[str, float | int], oi_change: float) -> None:
        side = -watch.sweep_direction
        structure = watch.structure_high if side > 0 else watch.structure_low
        details = self._details(watch, oi_change)
        self.pending = _construct(
            PendingSetup,
            scenario_id=watch.scenario_id,
            branch="REJECTION",
            side=side,
            swept_kind=watch.pool_kind,
            pool_id=watch.pool_id,
            pool_level=watch.pool_level,
            created_index=self.bar_index,
            created_ts=int(row["ts"]),
            expires_index=self.bar_index + self.config.rejection_confirmation_bars,
            sweep_extreme=watch.sweep_extreme,
            structure=structure,
            atr=watch.atr,
            hold_count=0,
            retrace_armed=False,
            details=details,
        )
        self.v41_watch = None
        self.diagnostics["rejection_setups"] += 1
        self.diagnostics["v41_rejection_selected"] += 1

    def _arm_acceptance(self, watch: CompetingSweep, row: dict[str, float | int], oi_change: float) -> None:
        if PositionBuildingSetup is None or not hasattr(self, "position_building_setups"):
            self.v41_watch = None
            self.diagnostics["v41_expired"] += 1
            return
        details = self._details(watch, oi_change)
        setup = _construct(
            PositionBuildingSetup,
            scenario_id=watch.scenario_id,
            side=watch.sweep_direction,
            pool_level=watch.pool_level,
            created_index=self.bar_index,
            created_ts=int(row["ts"]),
            expires_index=self.bar_index + self.config.acceptance_retrace_bars,
            hold_count=0,
            details=details,
        )
        self.position_building_setups.append(setup)
        self.v41_watch = None
        self.diagnostics["position_building_setups"] += 1
        self.diagnostics["v41_acceptance_selected"] += 1


CandidateStrategy = CompetingAuctionStrategy
StrategyClass = CompetingAuctionStrategy
