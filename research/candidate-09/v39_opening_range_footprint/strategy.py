"""Candidate 09 v39: funding-cycle opening range, footprint acceptance, first retest.

The strategy preserves V35's verified true-acceptance state machine, natural
liquidity objectives, risk sizing and NautilusTrader execution.  It changes the
entry context to one completed 15-minute range per eight-hour inventory/funding
cycle.  A range boundary may open one parent auction only; failed and unresolved
auctions are no-trades, while true acceptance still requires a boundary-crossing
price-level footprint stack and enters only on the first later defended retest.

The exact context control shifts the range by 15 minutes within the same cycle.
Frequency, state, entry, invalidation, target, costs and risk remain unchanged.
"""
from __future__ import annotations

import math
from typing import Any

from logic import Pool
from strategy_v35 import Candidate16Config as _Candidate35Config
from strategy_v35 import Candidate16Strategy as _Candidate35Strategy

_MINUTE_NS = 60_000_000_000
_CYCLE_MINUTES = 8 * 60


class Candidate16Config(_Candidate35Config, frozen=True):
    candidate39_range_minutes: int = 15
    candidate39_range_offset_minutes: int = 0
    candidate39_cycle_minutes: int = _CYCLE_MINUTES


class Candidate16Strategy(_Candidate35Strategy):
    """One opening-range interaction per completed eight-hour auction cycle."""

    def __init__(self, config: Candidate16Config) -> None:
        super().__init__(config=config)
        if config.candidate39_range_minutes != 15:
            raise ValueError("v39 pre-registration fixes the opening range at 15 minutes")
        if config.candidate39_cycle_minutes != _CYCLE_MINUTES:
            raise ValueError("v39 pre-registration fixes the inventory cycle at eight hours")
        if config.candidate39_range_offset_minutes not in (0, 15):
            raise ValueError("v39 permits only the opening range or its exact +15m control")
        self._candidate39_range_state: dict[str, float | int] | None = None
        self._candidate39_trigger_session: dict[str, int] = {}
        self._candidate39_closed_sessions: set[int] = set()
        self._candidate39_current_session: int | None = None
        self.diagnostics.update(
            {
                "candidate39_ranges_completed": 0,
                "candidate39_opening_offset_minutes": (
                    config.candidate39_range_offset_minutes
                ),
                "candidate39_boundary_interactions": 0,
                "candidate39_sessions_resolved": 0,
                "candidate39_sessions_expired": 0,
            }
        )

    def _candidate39_expire_session(
        self,
        session_key: int,
        row: dict[str, float | int],
        reason: str,
        *,
        count_as_expired: bool,
    ) -> None:
        pool_ids = [
            pool_id
            for pool_id, owner in self._candidate39_trigger_session.items()
            if owner == session_key
        ]
        for pool_id in pool_ids:
            pool = self.active_pools.pop(pool_id, None)
            self._candidate39_trigger_session.pop(pool_id, None)
            if pool is None:
                continue
            self._transition(
                pool.pool_id,
                "POOL_EXPIRED",
                int(row["ts"]),
                int(row["ts"]),
                "CLOSED",
                reason,
                pool.level,
                {"candidate39_session_key": session_key, "pool_source": pool.source},
            )
        if pool_ids and count_as_expired:
            self.diagnostics["candidate39_sessions_expired"] = int(
                self.diagnostics["candidate39_sessions_expired"]
            ) + 1

    def _add_pool(
        self,
        kind: str,
        level: float,
        event_time_ns: int,
        observed_time_ns: int,
        source: str,
        *,
        strength: int,
    ) -> None:
        """Keep higher-timeframe objective identity separate from trigger edges."""
        trigger_map = getattr(self, "_candidate39_trigger_session", {})
        trigger_pools = {
            pool_id: pool
            for pool_id, pool in self.active_pools.items()
            if pool_id in trigger_map
        }
        if not trigger_pools:
            super()._add_pool(
                kind,
                level,
                event_time_ns,
                observed_time_ns,
                source,
                strength=strength,
            )
            return
        self.active_pools = {
            pool_id: pool
            for pool_id, pool in self.active_pools.items()
            if pool_id not in trigger_pools
        }
        try:
            super()._add_pool(
                kind,
                level,
                event_time_ns,
                observed_time_ns,
                source,
                strength=strength,
            )
        finally:
            self.active_pools = {**self.active_pools, **trigger_pools}

    def _candidate39_add_trigger_pool(
        self,
        *,
        kind: str,
        level: float,
        start_ts_ns: int,
        observed_ts_ns: int,
        session_key: int,
    ) -> None:
        """Create a non-merging range edge so context identity cannot be lost."""
        self.pool_counter += 1
        pool_id = f"pool-{self.pool_counter:07d}"
        source = (
            "OPENING_RANGE_15M"
            if self.config.candidate39_range_offset_minutes == 0
            else "SECOND_RANGE_15M_CONTROL"
        )
        pool = Pool(
            pool_id=pool_id,
            kind=kind,
            level=level,
            event_time_ns=start_ts_ns,
            observed_time_ns=observed_ts_ns,
            source=source,
            strength=4,
            created_index=self.bar_index,
        )
        self.active_pools[pool_id] = pool
        self._candidate39_trigger_session[pool_id] = session_key
        self._transition(
            pool_id,
            "POOL_CONFIRMED",
            start_ts_ns,
            observed_ts_ns,
            "POOL_ARMED",
            source,
            level,
            {
                "kind": kind,
                "strength": 4,
                "candidate39_session_key": session_key,
                "candidate39_range_offset_minutes": (
                    self.config.candidate39_range_offset_minutes
                ),
            },
        )

    def _roll_session(self, row: dict[str, float | int]) -> None:
        """Build higher-timeframe objectives and one causal range per cycle."""
        super()._roll_session(row)
        ts_ns = int(row["ts"])
        open_minute = ts_ns // _MINUTE_NS - 1
        session_key = open_minute // self.config.candidate39_cycle_minutes
        session_start = session_key * self.config.candidate39_cycle_minutes

        if self._candidate39_current_session is None:
            self._candidate39_current_session = session_key
        elif session_key != self._candidate39_current_session:
            self._candidate39_expire_session(
                self._candidate39_current_session,
                row,
                "EIGHT_HOUR_OPENING_RANGE_SESSION_EXPIRED",
                count_as_expired=True,
            )
            self._candidate39_current_session = session_key
            self._candidate39_range_state = None

        range_start = session_start + self.config.candidate39_range_offset_minutes
        range_end = range_start + self.config.candidate39_range_minutes - 1
        if not (range_start <= open_minute <= range_end):
            return

        state = self._candidate39_range_state
        if state is None or int(state["session_key"]) != session_key:
            state = {
                "session_key": session_key,
                "start_ts_ns": ts_ns - _MINUTE_NS,
                "high": float(row["high"]),
                "low": float(row["low"]),
                "bars": 1,
            }
            self._candidate39_range_state = state
        else:
            state["high"] = max(float(state["high"]), float(row["high"]))
            state["low"] = min(float(state["low"]), float(row["low"]))
            state["bars"] = int(state["bars"]) + 1

        if open_minute != range_end or int(state["bars"]) != self.config.candidate39_range_minutes:
            return
        self._candidate39_add_trigger_pool(
            kind="HIGH",
            level=float(state["high"]),
            start_ts_ns=int(state["start_ts_ns"]),
            observed_ts_ns=ts_ns,
            session_key=session_key,
        )
        self._candidate39_add_trigger_pool(
            kind="LOW",
            level=float(state["low"]),
            start_ts_ns=int(state["start_ts_ns"]),
            observed_ts_ns=ts_ns,
            session_key=session_key,
        )
        self.diagnostics["candidate39_ranges_completed"] = int(
            self.diagnostics["candidate39_ranges_completed"]
        ) + 1
        self._candidate39_range_state = None

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        """Permit a parent auction only at the active cycle's range edges."""
        if self.parent_auction is not None:
            return
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return
        min_age = self.config.pool_min_age_bars
        eligible = {
            pool_id: pool
            for pool_id, pool in self.active_pools.items()
            if pool_id in self._candidate39_trigger_session
            and self._candidate39_trigger_session[pool_id]
            not in self._candidate39_closed_sessions
        }
        if not eligible:
            return

        high_crossed = [
            pool
            for pool in eligible.values()
            if pool.kind == "HIGH"
            and self.bar_index - pool.created_index >= min_age
            and previous_close <= pool.level
            and float(row["high"])
            >= pool.level + self.config.sweep_min_penetration_atr * atr
        ]
        low_crossed = [
            pool
            for pool in eligible.values()
            if pool.kind == "LOW"
            and self.bar_index - pool.created_index >= min_age
            and previous_close >= pool.level
            and float(row["low"])
            <= pool.level - self.config.sweep_min_penetration_atr * atr
        ]
        if not high_crossed and not low_crossed:
            return

        if high_crossed and low_crossed:
            resolved_sessions = {
                self._candidate39_trigger_session[pool.pool_id]
                for pool in high_crossed + low_crossed
            }
        elif high_crossed:
            selected = max(high_crossed, key=lambda item: (item.level, item.strength))
            resolved_sessions = {self._candidate39_trigger_session[selected.pool_id]}
        else:
            selected = min(low_crossed, key=lambda item: (item.level, -item.strength))
            resolved_sessions = {self._candidate39_trigger_session[selected.pool_id]}

        retained = {
            pool_id: pool
            for pool_id, pool in self.active_pools.items()
            if pool_id not in eligible
        }
        self.active_pools = dict(eligible)
        try:
            super()._detect_sweep(row, previous_close)
        finally:
            updated_triggers = self.active_pools
            self.active_pools = {**retained, **updated_triggers}

        for session_key in resolved_sessions:
            self._candidate39_closed_sessions.add(session_key)
            self.diagnostics["candidate39_boundary_interactions"] = int(
                self.diagnostics["candidate39_boundary_interactions"]
            ) + 1
            self._candidate39_expire_session(
                session_key,
                row,
                "OPENING_RANGE_PARENT_INTERACTION_ALREADY_RESOLVED",
                count_as_expired=False,
            )
            self.diagnostics["candidate39_sessions_resolved"] = int(
                self.diagnostics["candidate39_sessions_resolved"]
            ) + 1

        if self.pending is not None:
            self.pending.details.update(
                {
                    "candidate39_range_offset_minutes": (
                        self.config.candidate39_range_offset_minutes
                    ),
                    "candidate39_cycle_minutes": self.config.candidate39_cycle_minutes,
                    "candidate39_context": "FUNDING_CYCLE_OPENING_RANGE",
                }
            )


__all__ = ["Candidate16Config", "Candidate16Strategy"]
