#!/usr/bin/env python3
"""Candidate 05 v28: completed-session external liquidity alongside 5m execution."""
from __future__ import annotations

import math

from external_session_logic import utc_session_key
from external_session_logic import validate_uniform_session_hours
from strategy_base import LiquidityResponseConfig
from strategy_v26 import ScenarioValidEntryStrategy


class ExternalSessionLiquidityStrategy(ScenarioValidEntryStrategy):
    """Add completed activity-session highs/lows without replacing local structure.

    v3 replaced the entire five-minute pool universe with fifteen-minute pivots.
    That experiment had too few trades and one winner dominated its first week.
    v28 therefore retains the complete v26 five-minute detector and execution
    chain. It adds only the high and low of each fully completed, preconfigured
    four-hour UTC activity session as external liquidity observations.

    A session boundary is not itself an entry. The level must survive at least
    one completed minute, be penetrated with the unchanged activity minimum,
    reclaim with the unchanged absorption predicates, pass tail-flow and current
    depth checks, confirm the unchanged CHoCH sequence, retain a live opposing
    target, and satisfy the same costs, slippage and 3% current-NAV risk budget.

    When a session extreme is within the existing 0.10 ATR merge tolerance of a
    five-minute pool, the existing pool-strengthening transition records the
    multi-timeframe confluence. No score, fitted threshold or risk multiplier is
    introduced.
    """

    HIGH_SOURCE = "COMPLETED_4H_ACTIVITY_SESSION_HIGH"
    LOW_SOURCE = "COMPLETED_4H_ACTIVITY_SESSION_LOW"

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        span = validate_uniform_session_hours(tuple(config.session_hours))
        if span != 4:
            raise ValueError("v28 requires the frozen uniform four-hour session clock")
        self.external_session_key: int | None = None
        self.external_session_high = -math.inf
        self.external_session_low = math.inf
        self.external_session_high_ts = 0
        self.external_session_low_ts = 0
        self.diagnostics.update(
            {
                "external_session_span_hours": span,
                "completed_external_sessions": 0,
                "external_session_pool_observations": 0,
                "external_session_new_pools": 0,
                "external_session_cluster_merges": 0,
            },
        )

    def _update_five_minute(self, row: dict[str, float | int]) -> None:
        # Preserve the complete v26 local pool universe before adding one new
        # higher-order observation family.
        super()._update_five_minute(row)
        self._update_external_session(row)

    def _update_external_session(self, row: dict[str, float | int]) -> None:
        ts = int(row["ts"])
        key = utc_session_key(ts, tuple(self.config.session_hours))
        if self.external_session_key is None:
            self.external_session_key = key
        elif key != self.external_session_key:
            if (
                math.isfinite(self.external_session_high)
                and math.isfinite(self.external_session_low)
                and self.external_session_high_ts > 0
                and self.external_session_low_ts > 0
            ):
                self._add_external_session_pool(
                    "HIGH",
                    self.external_session_high,
                    self.external_session_high_ts,
                    ts,
                    self.HIGH_SOURCE,
                )
                self._add_external_session_pool(
                    "LOW",
                    self.external_session_low,
                    self.external_session_low_ts,
                    ts,
                    self.LOW_SOURCE,
                )
                self.diagnostics["completed_external_sessions"] += 1
                self.diagnostics["external_session_pool_observations"] += 2
            self.external_session_key = key
            self.external_session_high = -math.inf
            self.external_session_low = math.inf
            self.external_session_high_ts = 0
            self.external_session_low_ts = 0

        high = float(row["high"])
        low = float(row["low"])
        if high > self.external_session_high:
            self.external_session_high = high
            self.external_session_high_ts = ts
        if low < self.external_session_low:
            self.external_session_low = low
            self.external_session_low_ts = ts

    def _add_external_session_pool(
        self,
        kind: str,
        level: float,
        event_time_ns: int,
        observed_time_ns: int,
        source: str,
    ) -> None:
        before = self.pool_counter
        self._add_pool(
            kind,
            level,
            event_time_ns,
            observed_time_ns,
            source,
            strength=2,
        )
        if self.pool_counter > before:
            self.diagnostics["external_session_new_pools"] += 1
        else:
            self.diagnostics["external_session_cluster_merges"] += 1


__all__ = ["ExternalSessionLiquidityStrategy"]
