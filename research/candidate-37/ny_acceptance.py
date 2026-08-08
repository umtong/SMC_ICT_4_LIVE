"""Frozen causal New York accepted-auction policy for Candidate 37.

The policy uses only completed one-minute bars.  It observes two independently
past-known liquidity objectives during the New York morning: the previous UTC
day high/low and the four completed hours ending at 11:00 America/New_York.
The first break after 11:00 ET is actionable only when three completed closes
remain outside and the third close is at least 20 bp beyond the boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import math
from zoneinfo import ZoneInfo

import pandas as pd

NY = ZoneInfo("America/New_York")
UTC = timezone.utc


@dataclass(frozen=True, slots=True)
class NYAcceptanceConfig:
    break_bps: float = 5.0
    final_extension_bps: float = 20.0
    confirmation_bars: int = 3
    minimum_price_risk_bps: float = 10.0
    maximum_price_risk_bps: float = 90.0
    target_r: float = 2.6
    round_trip_cost_bps: float = 21.0
    horizon_minutes: int = 300


@dataclass(frozen=True, slots=True)
class LiquidityLevel:
    name: str
    high: float
    low: float
    priority: int


@dataclass(frozen=True, slots=True)
class AcceptanceSignal:
    symbol: str
    local_day: date
    level_name: str
    level_priority: int
    side: int
    break_index: int
    signal_index: int
    signal_time: pd.Timestamp
    boundary: float
    stop: float
    target: float
    risk_bps: float
    reward_bps: float
    score: float


def _utc(local_day: date, hour: int) -> pd.Timestamp:
    local = datetime.combine(local_day, time(hour=hour), tzinfo=NY)
    return pd.Timestamp(local.astimezone(UTC))


def session_times(local_day: date) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    return _utc(local_day, 7), _utc(local_day, 11), _utc(local_day, 11), _utc(local_day, 14)


def _bps(side: int, start: float, end: float) -> float:
    return side * (end - start) / start * 10_000.0


def build_levels(frame: pd.DataFrame, local_day: date) -> list[LiquidityLevel]:
    pre_start, pre_end, trade_start, _ = session_times(local_day)
    utc_midnight = trade_start.floor("D")
    previous = frame[(frame["time"] >= utc_midnight - pd.Timedelta(days=1)) & (frame["time"] < utc_midnight)]
    pre_four = frame[(frame["time"] >= pre_start) & (frame["time"] < pre_end)]
    levels: list[LiquidityLevel] = []
    if len(previous) == 1440:
        levels.append(LiquidityLevel("PREVIOUS_UTC_DAY", float(previous["high"].max()), float(previous["low"].min()), 2))
    if len(pre_four) == 240:
        levels.append(LiquidityLevel("PRE_NY_FOUR_HOUR", float(pre_four["high"].max()), float(pre_four["low"].min()), 1))
    return levels


def first_accepted_break(
    *, symbol: str, frame: pd.DataFrame, local_day: date, level: LiquidityLevel,
    config: NYAcceptanceConfig,
) -> AcceptanceSignal | None:
    _, _, trade_start, trade_end = session_times(local_day)
    indices = frame.index[(frame["time"] >= trade_start) & (frame["time"] < trade_end)].tolist()
    for index in indices:
        row = frame.iloc[index]
        up = (float(row["high"]) - level.high) / level.high * 10_000.0
        down = (level.low - float(row["low"])) / level.low * 10_000.0
        directions: list[tuple[int, float, float]] = []
        if up >= config.break_bps:
            directions.append((1, level.high, up))
        if down >= config.break_bps:
            directions.append((-1, level.low, down))
        if not directions:
            continue
        # The first causal episode consumes this level.  A two-sided shock and a
        # failed first break are unresolved; the level is never recycled.
        if len(directions) != 1:
            return None
        side, boundary, penetration = directions[0]
        signal_index = index + config.confirmation_bars - 1
        if signal_index + 1 >= len(frame):
            return None
        confirmation = frame.iloc[index : signal_index + 1]
        final = frame.iloc[signal_index]
        extension = side * (float(final["close"]) - boundary) / boundary * 10_000.0
        holds = bool((confirmation["close"] >= boundary).all()) if side > 0 else bool((confirmation["close"] <= boundary).all())
        if extension < config.final_extension_bps or not holds:
            return None
        entry = float(frame.iloc[signal_index + 1]["open"])
        stop = float(confirmation["low"].min()) if side > 0 else float(confirmation["high"].max())
        risk = -_bps(side, entry, stop)
        if not config.minimum_price_risk_bps <= risk <= config.maximum_price_risk_bps:
            return None
        reward = risk * config.target_r
        target = entry * (1.0 + side * reward / 10_000.0)
        values = (entry, stop, target, risk, reward, extension, penetration)
        if not all(math.isfinite(value) for value in values):
            return None
        return AcceptanceSignal(
            symbol=symbol,
            local_day=local_day,
            level_name=level.name,
            level_priority=level.priority,
            side=side,
            break_index=index,
            signal_index=signal_index,
            signal_time=pd.Timestamp(final["time"]),
            boundary=boundary,
            stop=stop,
            target=target,
            risk_bps=risk,
            reward_bps=reward,
            score=level.priority * 1_000.0 + extension + penetration,
        )
    return None
