"""Quarter-hour algorithmic-flow router with positioning and L1 state.

This adds an independent scenario family to the existing regional router.  The
first ten seconds of a 15-minute boundary are the parent event; completed-minute
price delivery, spot participation, OI, tail flow and closing L1 pressure route
that event to continuation, reversal, or no trade.  NautilusTrader remains the
only execution, portfolio and NAV owner.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

from l1_pressure_router import failure_pressure_transition
from l1_pressure_router import pressure_persistence
from positioning_strategy import PositioningRegionalConfig
from positioning_strategy import PositioningRegionalStrategy
from quarter_hour_logic import QuarterObservation
from quarter_hour_logic import QuarterRoute
from quarter_hour_logic import route_quarter_hour
from strategy_base import PendingSetup


class QuarterHourPositioningConfig(PositioningRegionalConfig, frozen=True):
    pass


class QuarterHourPositioningStrategy(PositioningRegionalStrategy):
    """Arbitrate regional auctions first, then independent quarter-hour events."""

    def __init__(self, config: QuarterHourPositioningConfig) -> None:
        super().__init__(config=config)
        self._quarter_key: tuple[int, int, int, int, int] | None = None
        self._quarter_range: dict[str, float | int] | None = None
        self.diagnostics.update(
            {
                "quarter_ranges_completed": 0,
                "quarter_boundary_observations": 0,
                "quarter_opening_bursts": 0,
                "quarter_new_risk_continuations": 0,
                "quarter_forced_closure_reversals": 0,
                "quarter_unresolved": 0,
                "quarter_entry_submissions": 0,
            },
        )

    @staticmethod
    def _utc(ts_ns: int) -> datetime:
        return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc)

    @classmethod
    def _quarter_clock_key(cls, ts_ns: int) -> tuple[int, int, int, int, int]:
        moment = cls._utc(ts_ns)
        return (
            moment.year,
            moment.month,
            moment.day,
            moment.hour,
            moment.minute // 15,
        )

    @classmethod
    def _is_quarter_opening_minute(cls, ts_ns: int) -> bool:
        return cls._utc(ts_ns).minute % 15 == 0

    def _finish_quarter_range(self, observed_ns: int) -> None:
        state = self._quarter_range
        key = self._quarter_key
        if state is None or key is None:
            return
        source = (
            "SESSION_4H:QUARTER:"
            f"{key[0]:04d}-{key[1]:02d}-{key[2]:02d}T"
            f"{key[3]:02d}:{key[4] * 15:02d}Z"
        )
        self._add_pool(
            "HIGH",
            float(state["high"]),
            int(state["high_ts"]),
            observed_ns,
            source,
            strength=1,
        )
        self._add_pool(
            "LOW",
            float(state["low"]),
            int(state["low_ts"]),
            observed_ns,
            source,
            strength=1,
        )
        self.diagnostics["quarter_ranges_completed"] += 1

    def _update_quarter_range(self, row: dict[str, float | int]) -> None:
        ts_ns = int(row["ts"])
        moment = self._utc(ts_ns)
        key = self._quarter_clock_key(ts_ns)
        if self._quarter_key != key or self._quarter_range is None:
            self._quarter_key = key
            self._quarter_range = {
                "high": float(row["high"]),
                "low": float(row["low"]),
                "high_ts": ts_ns,
                "low_ts": ts_ns,
            }
        else:
            state = self._quarter_range
            if float(row["high"]) > float(state["high"]):
                state["high"] = float(row["high"])
                state["high_ts"] = ts_ns
            if float(row["low"]) < float(state["low"]):
                state["low"] = float(row["low"])
                state["low_ts"] = ts_ns

        # At the close of minutes 14/29/44/59 the entire quarter range is
        # known.  Publish it then, so the next opening minute can consume it
        # without any one-bar observation delay.
        if (moment.minute + 1) % 15 == 0:
            self._finish_quarter_range(ts_ns)
            self._quarter_key = None
            self._quarter_range = None

    def _roll_session(self, row: dict[str, float | int]) -> None:
        super()._roll_session(row)
        self._update_quarter_range(row)

    def _prune_pools(self, row: dict[str, float | int]) -> None:
        super()._prune_pools(row)
        consumed = [
            pool
            for pool in self.active_pools.values()
            if pool.source.startswith("SESSION_4H:QUARTER:")
            and self.bar_index > pool.created_index
            and (
                (pool.kind == "HIGH" and float(row["high"]) >= pool.level)
                or (pool.kind == "LOW" and float(row["low"]) <= pool.level)
            )
        ]
        for pool in consumed:
            self._consume_pool(pool, row, "QUARTER_RANGE_OBJECTIVE_ACCESSED")

    def _quarter_observation(self, parent_direction: int) -> QuarterObservation:
        pressure = self._pressure_observation()
        return QuarterObservation(
            opening_flow_10s=self._finite_feature("flow_open_10s"),
            opening_notional_burst=self._finite_feature(
                "notional_open_10s_burst",
            ),
            perpetual_return_bps=self._finite_feature("ret_60s_bps"),
            tail_flow_15s=self._finite_feature("flow_15s"),
            spot_flow_60s=self._finite_feature("spot_flow_60s"),
            spot_return_bps=self._finite_feature("spot_ret_60s_bps"),
            oi_change_5m=self._finite_feature("oi_change_5m"),
            oi_value_change_5m=self._finite_feature("oi_value_change_5m"),
            l1_pressure_persisted=pressure_persistence(
                parent_direction,
                pressure,
            ),
            l1_pressure_flipped=failure_pressure_transition(
                parent_direction,
                pressure,
            ),
        )

    def _detect_sweep(
        self,
        row: dict[str, float | int],
        previous_close: float,
    ) -> None:
        # Existing regional parent interactions retain priority.  This is a
        # causal arbitration rule, not an outcome-ranked choice.
        super()._detect_sweep(row, previous_close)
        if self.parent_auction is not None or self.pending is not None:
            return
        if not self._is_quarter_opening_minute(int(row["ts"])):
            return

        self.diagnostics["quarter_boundary_observations"] += 1
        opening_flow = self._finite_feature("flow_open_10s")
        opening_burst = self._finite_feature("notional_open_10s_burst")
        if (
            not math.isfinite(opening_flow)
            or not math.isfinite(opening_burst)
            or opening_flow == 0.0
            or opening_burst <= 1.0
        ):
            return

        parent_direction = 1 if opening_flow > 0.0 else -1
        try:
            observation = self._quarter_observation(parent_direction)
            decision = route_quarter_hour(observation)
        except ValueError:
            self.diagnostics["quarter_unresolved"] += 1
            return
        if decision.route is QuarterRoute.NO_EVENT:
            return

        self.diagnostics["quarter_opening_bursts"] += 1
        self.scenario_counter += 1
        scenario_id = f"qhp-{self.scenario_counter:07d}"
        details: dict[str, Any] = {
            "candidate11_family": "QUARTER_HOUR_POSITIONING",
            "quarter_parent_event": {
                "parent_direction": decision.parent_direction,
                "opening_flow_10s": observation.opening_flow_10s,
                "opening_notional_burst": observation.opening_notional_burst,
            },
            "positioning_context": {
                "oi_change_5m": observation.oi_change_5m,
                "oi_value_change_5m": observation.oi_value_change_5m,
            },
            "completed_delivery": {
                "perpetual_return_bps": observation.perpetual_return_bps,
                "tail_flow_15s": observation.tail_flow_15s,
                "spot_flow_60s": observation.spot_flow_60s,
                "spot_return_bps": observation.spot_return_bps,
                "l1_pressure_persisted": observation.l1_pressure_persisted,
                "l1_pressure_flipped": observation.l1_pressure_flipped,
            },
            "quarter_route": decision.route.value,
            "route_reason": decision.reason,
        }

        if decision.route is QuarterRoute.UNRESOLVED:
            self.diagnostics["quarter_unresolved"] += 1
            self._transition(
                scenario_id,
                "QUARTER_HOUR_STATE_ROUTED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                decision.reason,
                float(row["close"]),
                details,
            )
            return

        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            self.diagnostics["quarter_unresolved"] += 1
            return
        continuation = decision.route is QuarterRoute.NEW_RISK_CONTINUATION
        branch = "ACCEPTANCE" if continuation else "REJECTION"
        if continuation:
            self.diagnostics["quarter_new_risk_continuations"] += 1
        else:
            self.diagnostics["quarter_forced_closure_reversals"] += 1

        parent_extreme = (
            float(row["high"])
            if decision.parent_direction > 0
            else float(row["low"])
        )
        setup = PendingSetup(
            scenario_id=scenario_id,
            branch=branch,
            side=decision.side,
            swept_kind="HIGH" if decision.parent_direction > 0 else "LOW",
            pool_id=scenario_id,
            pool_level=float(row["open"]),
            created_index=self.bar_index,
            expires_index=self.bar_index,
            sweep_extreme=parent_extreme,
            structure=float(row["open"]),
            atr=atr,
            hold_count=0,
            retrace_armed=False,
            details=details,
        )
        self.pending = setup
        self._transition(
            scenario_id,
            "QUARTER_HOUR_STATE_ROUTED",
            int(row["ts"]),
            int(row["ts"]),
            "ENTRY_EVALUATION",
            decision.reason,
            float(row["close"]),
            details,
        )
        submitted = self._submit_entry(setup, row)
        self.diagnostics["quarter_entry_submissions"] += int(submitted)


__all__ = [
    "QuarterHourPositioningConfig",
    "QuarterHourPositioningStrategy",
]
