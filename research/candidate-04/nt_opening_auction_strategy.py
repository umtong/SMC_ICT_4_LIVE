#!/usr/bin/env python3
"""Candidate-04 v10: session opening-auction acceptance and first retest.

Each eight-hour funding session is treated as a fresh auction. The initial
30/60-minute range is fixed before any signal can exist. A trade requires the
following causal sequence:

1. displacement closes outside the fixed opening range with participation;
2. a second completed bar accepts price outside the range;
3. the first return to the broken boundary is defended and closes back outside;
4. entry is submitted through NautilusTrader with invalidation beyond the
   defended retest and a measured opening-range liquidity objective.

A failed acceptance is not reversed automatically. The setup is simply retired.
All fills, contingent orders, fees, positions, margin and NAV are handled only
by NautilusTrader.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from nt_liquidity_strategy import LiquidityTransitionConfig
from nt_liquidity_strategy import LiquidityTransitionStrategy
from nt_liquidity_strategy import PendingSetup


NANOS_PER_MINUTE = 60_000_000_000
SESSION_MINUTES = 8 * 60
SCENARIO = "OPENING_AUCTION_ACCEPTANCE_RETEST"


@dataclass(slots=True)
class OpeningBreakout:
    side: int
    boundary: float
    range_high: float
    range_low: float
    range_width: float
    created_index: int
    acceptance_expires_index: int
    acceptance_closes: int
    accepted_index: int | None = None
    retest_expires_index: int | None = None


def session_coordinates(ts_event: int) -> tuple[int, int]:
    minute = int(ts_event) // NANOS_PER_MINUTE
    return minute // SESSION_MINUTES, minute % SESSION_MINUTES


class OpeningAuctionAcceptanceStrategy(LiquidityTransitionStrategy):
    OPENING_MINUTES = 60
    BREAKOUT_MIN_ATR = 0.10
    BREAKOUT_BODY_ATR = 0.50
    BREAKOUT_VOLUME_BURST = 1.20
    BREAKOUT_CLOSE_LOCATION = 0.70
    ACCEPTANCE_BARS = 6
    ACCEPTANCE_DISTANCE_ATR = 0.04
    RETEST_BARS = 35
    RETEST_TOUCH_ATR = 0.12
    RETEST_CLOSE_LOCATION = 0.55
    INVALIDATION_INSIDE_ATR = 0.25
    MIN_RANGE_ATR = 2.0
    MAX_RANGE_ATR = 18.0
    TARGET_NET_R = 1.60

    def __init__(self, config: LiquidityTransitionConfig) -> None:
        super().__init__(config)
        self.opening_session_id: int | None = None
        self.breakout: OpeningBreakout | None = None
        self.used_breakout_sides: set[int] = set()
        self.session_trade_submitted = False

    def _reset_opening_session(self, session_id: int) -> None:
        self.opening_session_id = session_id
        self.breakout = None
        self.used_breakout_sides.clear()
        self.session_trade_submitted = False

    def _session_rows(self, session_id: int) -> list[dict[str, float | int]]:
        selected: list[dict[str, float | int]] = []
        for item in self.bars:
            item_session, _ = session_coordinates(int(item["ts"]))
            if item_session == session_id:
                selected.append(item)
        return selected

    def _detect_session_sweep(self, row: dict[str, float | int]) -> bool:
        session_id, offset = session_coordinates(int(row["ts"]))
        if session_id != self.opening_session_id:
            self._reset_opening_session(session_id)

        if self.session_trade_submitted or offset < self.OPENING_MINUTES:
            return False

        session_rows = self._session_rows(session_id)
        opening_rows = [
            item
            for item in session_rows
            if session_coordinates(int(item["ts"]))[1] < self.OPENING_MINUTES
        ]
        if len(opening_rows) < self.OPENING_MINUTES:
            return False
        range_high = max(float(item["high"]) for item in opening_rows)
        range_low = min(float(item["low"]) for item in opening_rows)
        range_width = range_high - range_low
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return False
        range_atr = range_width / atr
        if not self.MIN_RANGE_ATR <= range_atr <= self.MAX_RANGE_ATR:
            return False

        if self.breakout is None:
            return self._detect_displacement(
                row,
                range_high,
                range_low,
                range_width,
                range_atr,
                atr,
            )
        return self._advance_opening_breakout(row, atr)

    def _detect_displacement(
        self,
        row: dict[str, float | int],
        range_high: float,
        range_low: float,
        range_width: float,
        range_atr: float,
        atr: float,
    ) -> bool:
        close = float(row["close"])
        if close >= range_high + self.BREAKOUT_MIN_ATR * atr:
            side = 1
            boundary = range_high
        elif close <= range_low - self.BREAKOUT_MIN_ATR * atr:
            side = -1
            boundary = range_low
        else:
            return False
        if side in self.used_breakout_sides:
            return False

        body = side * (close - float(row["open"])) / atr
        volume_burst = self._volume_burst()
        close_location = self._close_location(row, side)
        if not (
            body >= self.BREAKOUT_BODY_ATR
            and volume_burst >= self.BREAKOUT_VOLUME_BURST
            and close_location >= self.BREAKOUT_CLOSE_LOCATION
        ):
            return False

        self.used_breakout_sides.add(side)
        self.breakout = OpeningBreakout(
            side=side,
            boundary=boundary,
            range_high=range_high,
            range_low=range_low,
            range_width=range_width,
            created_index=self.bar_index,
            acceptance_expires_index=self.bar_index + self.ACCEPTANCE_BARS,
            acceptance_closes=1,
        )
        self._event(
            "OPENING_RANGE_DISPLACEMENT",
            SCENARIO,
            row,
            {
                "side": side,
                "boundary": boundary,
                "opening_range_high": range_high,
                "opening_range_low": range_low,
                "opening_range_width": range_width,
                "opening_range_atr": range_atr,
                "breakout_body_atr": body,
                "breakout_volume_burst": volume_burst,
                "breakout_close_location": close_location,
                "opening_minutes": self.OPENING_MINUTES,
            },
        )
        return True

    def _advance_opening_breakout(
        self,
        row: dict[str, float | int],
        atr: float,
    ) -> bool:
        probe = self.breakout
        if probe is None:
            return False
        side = probe.side
        close = float(row["close"])
        outside = side * (close - probe.boundary)

        if probe.accepted_index is None:
            if self.bar_index > probe.acceptance_expires_index:
                self._event("OPENING_ACCEPTANCE_EXPIRED", SCENARIO, row, {"side": side})
                self.breakout = None
                return True
            if outside <= 0.0:
                self._event("OPENING_BREAKOUT_REJECTED", SCENARIO, row, {"side": side})
                self.breakout = None
                return True
            if outside >= self.ACCEPTANCE_DISTANCE_ATR * atr:
                probe.acceptance_closes += 1
            else:
                probe.acceptance_closes = 0
            if probe.acceptance_closes >= 2:
                probe.accepted_index = self.bar_index
                probe.retest_expires_index = self.bar_index + self.RETEST_BARS
                self._event(
                    "OPENING_PRICE_ACCEPTED",
                    SCENARIO,
                    row,
                    {
                        "side": side,
                        "boundary": probe.boundary,
                        "acceptance_closes": probe.acceptance_closes,
                    },
                )
            return True

        assert probe.retest_expires_index is not None
        if self.bar_index > probe.retest_expires_index:
            self._event("OPENING_RETEST_EXPIRED", SCENARIO, row, {"side": side})
            self.breakout = None
            return True
        if outside < -self.INVALIDATION_INSIDE_ATR * atr:
            self._event("OPENING_ACCEPTANCE_INVALIDATED", SCENARIO, row, {"side": side})
            self.breakout = None
            return True
        if self.bar_index <= probe.accepted_index:
            return True

        touched = (
            float(row["low"]) <= probe.boundary + self.RETEST_TOUCH_ATR * atr
            if side > 0
            else float(row["high"]) >= probe.boundary - self.RETEST_TOUCH_ATR * atr
        )
        defended = outside > 0.0 and self._close_location(row, side) >= self.RETEST_CLOSE_LOCATION
        if not (touched and defended):
            return True

        retest_extreme = float(row["low"] if side > 0 else row["high"])
        measured_target = probe.boundary + side * probe.range_width
        setup = PendingSetup(
            scenario=SCENARIO,
            side=side,
            created_index=probe.created_index,
            expires_index=self.bar_index,
            extreme=retest_extreme,
            structure=probe.boundary,
            atr=atr,
            target_reference=measured_target,
            details={
                "side": side,
                "boundary": probe.boundary,
                "opening_range_high": probe.range_high,
                "opening_range_low": probe.range_low,
                "opening_range_width": probe.range_width,
                "accepted_index": probe.accepted_index,
                "retest_delay_bars": self.bar_index - probe.accepted_index,
                "retest_close_location": self._close_location(row, side),
                "measured_target": measured_target,
                "opening_minutes": self.OPENING_MINUTES,
            },
        )
        self._event("OPENING_RETEST_DEFENDED", SCENARIO, row, setup.details)
        submitted = LiquidityTransitionStrategy._submit_bracket(
            self,
            setup,
            row,
            self.TARGET_NET_R,
            setup.details,
        )
        if submitted:
            self.session_trade_submitted = True
        else:
            self._event("OPENING_RETEST_EXECUTION_REJECTED", SCENARIO, row, setup.details)
        self.breakout = None
        return True

    def _detect_trend_sweep(self, row: dict[str, float | int]) -> bool:
        return False


class OpeningAuction30Strategy(OpeningAuctionAcceptanceStrategy):
    OPENING_MINUTES = 30


class OpeningAuction60Strategy(OpeningAuctionAcceptanceStrategy):
    OPENING_MINUTES = 60


__all__ = [
    "LiquidityTransitionConfig",
    "OpeningAuction30Strategy",
    "OpeningAuction60Strategy",
    "OpeningBreakout",
    "session_coordinates",
]
