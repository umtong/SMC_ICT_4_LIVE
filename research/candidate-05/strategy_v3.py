#!/usr/bin/env python3
"""Candidate 05 v3: 15m external liquidity with the unchanged v2 retrace entry."""
from __future__ import annotations

from logic import is_confirmed_pivot
from retrace_logic import aggregate_completed_bar
from strategy_base import LiquidityResponseConfig
from strategy_v2 import LiquidityResponseRetraceStrategy


MINUTE_NS = 60_000_000_000
LIQUIDITY_MINUTES = 15


class LiquidityResponseExternalRetraceStrategy(LiquidityResponseRetraceStrategy):
    """Use completed 15m swing liquidity; keep response and execution unchanged."""

    def __init__(self, config: LiquidityResponseConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "liquidity_timeframe_minutes": LIQUIDITY_MINUTES,
                "fifteen_minute_bars": 0,
                "fifteen_minute_pools": 0,
                "incomplete_fifteen_minute_buckets": 0,
            },
        )

    def _update_five_minute(self, row: dict[str, float | int]) -> None:
        """Override only the completed liquidity-event scale (15m instead of 5m)."""
        minute = int(row["ts"]) // MINUTE_NS
        bucket = minute // LIQUIDITY_MINUTES
        if self.five_bucket is None:
            self.five_bucket = bucket
        elif bucket != self.five_bucket:
            if self.five_rows:
                self.diagnostics["incomplete_fifteen_minute_buckets"] += 1
            self.five_rows = []
            self.five_bucket = bucket
        self.five_rows.append(row.copy())
        if minute % LIQUIDITY_MINUTES != LIQUIDITY_MINUTES - 1:
            return
        if len(self.five_rows) != LIQUIDITY_MINUTES:
            self.diagnostics["incomplete_fifteen_minute_buckets"] += 1
        else:
            self.five_bars.append(aggregate_completed_bar(self.five_rows))
            self.diagnostics["fifteen_minute_bars"] += 1
            self._confirm_five_pivot(int(row["ts"]))
        self.five_rows = []
        self.five_bucket = None

    def _confirm_five_pivot(self, observed_ns: int) -> None:
        span = self.config.pivot_span
        rows = list(self.five_bars)
        if len(rows) < 2 * span + 1:
            return
        window = rows[-(2 * span + 1) :]
        center = window[span]
        highs = [float(item["high"]) for item in window]
        lows = [float(item["low"]) for item in window]
        if is_confirmed_pivot(highs, span=span, kind="HIGH"):
            self._add_pool(
                "HIGH",
                float(center["high"]),
                int(center["ts"]),
                observed_ns,
                "CONFIRMED_15M_EXTERNAL_SWING",
                strength=1,
            )
            self.diagnostics["fifteen_minute_pools"] += 1
        if is_confirmed_pivot(lows, span=span, kind="LOW"):
            self._add_pool(
                "LOW",
                float(center["low"]),
                int(center["ts"]),
                observed_ns,
                "CONFIRMED_15M_EXTERNAL_SWING",
                strength=1,
            )
            self.diagnostics["fifteen_minute_pools"] += 1


__all__ = ["LiquidityResponseExternalRetraceStrategy"]
