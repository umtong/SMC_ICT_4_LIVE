"""Causal impact -> MSS/displacement -> FVG first-retest state machine.

This module is a structural scenario detector, not a backtest engine. It creates
no orders, fills, cash ledger, PnL or NAV. Qualified upstream liquidity-impact
events are accepted only after a completed one-minute market-structure shift
with ranked displacement and an unfilled three-candle fair-value gap. Baseline
entry requires the first episode-bounded FVG retest to reject back through its
proximal edge. A controlled ablation can remove only that retest requirement and
enter at the completed MSS close while retaining all source-event, MSS,
displacement and FVG requirements.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

import numpy as np
import pandas as pd

Direction = Literal["LONG", "SHORT"]


@dataclass(frozen=True, slots=True)
class ImpactEvent:
    event_id: str
    direction: Direction
    event_end_ns: int
    source_pool_id: str
    source_level: float
    event_extreme: float


@dataclass(frozen=True, slots=True)
class Swing:
    swing_id: str
    side: Literal["UPPER", "LOWER"]
    level: float
    pivot_ns: int
    confirmed_ns: int


@dataclass(frozen=True, slots=True)
class FairValueGap:
    gap_id: str
    direction: Direction
    lower: float
    upper: float
    confirmed_ns: int
    source_index: int


@dataclass(frozen=True, slots=True)
class ImpactMSSFVGLogic:
    pivot_radius: int = 2
    displacement_rank_period: int = 60
    minimum_displacement_rank: float = 0.80
    minimum_body_atr: float = 0.15
    minimum_close_location: float = 0.65
    maximum_mss_minutes: int = 5
    maximum_retest_minutes: int = 5
    retest_close_location: float = 0.55
    stop_buffer_atr: float = 0.05

    def validate(self) -> None:
        for name in (
            "pivot_radius",
            "displacement_rank_period",
            "maximum_mss_minutes",
            "maximum_retest_minutes",
        ):
            if int(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in (
            "minimum_displacement_rank",
            "minimum_close_location",
            "retest_close_location",
        ):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        if self.minimum_body_atr <= 0.0:
            raise ValueError("minimum_body_atr must be positive")
        if self.stop_buffer_atr < 0.0:
            raise ValueError("stop_buffer_atr must be non-negative")


@dataclass(frozen=True, slots=True)
class EntryPlan:
    scenario_id: str
    event_id: str
    direction: Direction
    observed_ns: int
    entry: float
    stop: float
    source_level: float
    source_pool_id: str
    mss_swing_id: str
    mss_level: float
    mss_ns: int
    fvg_id: str
    fvg_lower: float
    fvg_upper: float
    retest_required: bool
    retest_ns: int | None
    body_atr: float
    displacement_rank: float


@dataclass(frozen=True, slots=True)
class EventDiagnostic:
    event_id: str
    outcome: str
    details: dict[str, object]


def minute_features(minutes: pd.DataFrame, *, rank_period: int) -> pd.DataFrame:
    required = {"timestamp_ns", "open", "high", "low", "close", "atr"}
    missing = required.difference(minutes.columns)
    if missing:
        raise ValueError(f"minute columns missing: {sorted(missing)}")
    work = minutes.copy().sort_values("timestamp_ns", kind="stable").reset_index(drop=True)
    work["timestamp_ns"] = work["timestamp_ns"].astype("int64")
    if work["timestamp_ns"].duplicated().any():
        raise ValueError("minute timestamps must be unique")
    body = (work["close"] - work["open"]).abs()
    work["body_atr"] = body / work["atr"].replace(0.0, np.nan)
    ranks: list[float] = []
    values = work["body_atr"].to_numpy(dtype=float)
    for index, value in enumerate(values):
        start = max(0, index - rank_period)
        history = values[start:index]
        history = history[np.isfinite(history)]
        ranks.append(
            float(np.mean(history <= value))
            if len(history) >= rank_period
            else np.nan
        )
    work["displacement_rank"] = ranks
    range_ = (work["high"] - work["low"]).replace(0.0, np.nan)
    work["close_location"] = (work["close"] - work["low"]) / range_
    return work


def confirmed_swings(minutes: pd.DataFrame, *, radius: int) -> list[Swing]:
    if radius <= 0:
        raise ValueError("radius must be positive")
    timestamps = minutes["timestamp_ns"].astype("int64").to_numpy()
    highs = minutes["high"].astype(float).to_numpy()
    lows = minutes["low"].astype(float).to_numpy()
    output: list[Swing] = []
    for center in range(radius, len(minutes.index) - radius):
        left = slice(center - radius, center)
        right = slice(center + 1, center + radius + 1)
        pivot_ns = int(timestamps[center])
        confirmed_ns = int(timestamps[center + radius])
        if highs[center] > np.max(highs[left]) and highs[center] > np.max(highs[right]):
            output.append(
                Swing(
                    f"1MH-{pivot_ns}",
                    "UPPER",
                    float(highs[center]),
                    pivot_ns,
                    confirmed_ns,
                )
            )
        if lows[center] < np.min(lows[left]) and lows[center] < np.min(lows[right]):
            output.append(
                Swing(
                    f"1ML-{pivot_ns}",
                    "LOWER",
                    float(lows[center]),
                    pivot_ns,
                    confirmed_ns,
                )
            )
    output.sort(key=lambda item: (item.confirmed_ns, item.swing_id))
    return output


def _latest_mss_swing(
    swings: Iterable[Swing],
    *,
    event: ImpactEvent,
) -> Swing | None:
    side = "UPPER" if event.direction == "LONG" else "LOWER"
    eligible = [
        item
        for item in swings
        if item.side == side and item.confirmed_ns <= event.event_end_ns
    ]
    return (
        max(eligible, key=lambda item: (item.confirmed_ns, item.pivot_ns))
        if eligible
        else None
    )


def _directional_displacement(
    row: pd.Series,
    *,
    direction: Direction,
    logic: ImpactMSSFVGLogic,
) -> bool:
    body = float(row["close"] - row["open"])
    location = float(row["close_location"])
    rank = float(row["displacement_rank"])
    body_atr = float(row["body_atr"])
    if not np.isfinite(rank) or not np.isfinite(body_atr) or not np.isfinite(location):
        return False
    if body_atr < logic.minimum_body_atr or rank < logic.minimum_displacement_rank:
        return False
    if direction == "LONG":
        return body > 0.0 and location >= logic.minimum_close_location
    return body < 0.0 and location <= 1.0 - logic.minimum_close_location


def _fvg_at(
    minutes: pd.DataFrame,
    index: int,
    *,
    direction: Direction,
) -> FairValueGap | None:
    if index < 2:
        return None
    first = minutes.iloc[index - 2]
    third = minutes.iloc[index]
    confirmed_ns = int(third["timestamp_ns"])
    if direction == "LONG":
        lower = float(first["high"])
        upper = float(third["low"])
        if upper <= lower:
            return None
    else:
        lower = float(third["high"])
        upper = float(first["low"])
        if upper <= lower:
            return None
    return FairValueGap(
        gap_id=f"FVG-{direction}-{confirmed_ns}",
        direction=direction,
        lower=lower,
        upper=upper,
        confirmed_ns=confirmed_ns,
        source_index=index,
    )


def diagnose(
    minutes: pd.DataFrame,
    *,
    events: Iterable[ImpactEvent],
    logic: ImpactMSSFVGLogic,
    require_fvg_retest: bool = True,
) -> tuple[list[EntryPlan], list[EventDiagnostic]]:
    logic.validate()
    work = minute_features(minutes, rank_period=logic.displacement_rank_period)
    swings = confirmed_swings(work, radius=logic.pivot_radius)
    timestamps = work["timestamp_ns"].astype("int64").to_numpy()
    plans: list[EntryPlan] = []
    diagnostics: list[EventDiagnostic] = []

    for event in sorted(events, key=lambda item: (item.event_end_ns, item.event_id)):
        swing = _latest_mss_swing(swings, event=event)
        if swing is None:
            diagnostics.append(
                EventDiagnostic(event.event_id, "NO_CAUSAL_MSS_SWING", {})
            )
            continue
        first_index = int(np.searchsorted(timestamps, event.event_end_ns, side="right"))
        last_index = min(len(work.index), first_index + logic.maximum_mss_minutes)
        mss_index: int | None = None
        fvg: FairValueGap | None = None
        for index in range(first_index, last_index):
            row = work.iloc[index]
            crossed = (
                float(row["close"]) > swing.level
                if event.direction == "LONG"
                else float(row["close"]) < swing.level
            )
            if not crossed or not _directional_displacement(
                row,
                direction=event.direction,
                logic=logic,
            ):
                continue
            candidate_gap = _fvg_at(work, index, direction=event.direction)
            if candidate_gap is None:
                diagnostics.append(
                    EventDiagnostic(
                        event.event_id,
                        "MSS_WITHOUT_CAUSAL_FVG",
                        {
                            "mss_ns": int(row["timestamp_ns"]),
                            "swing_id": swing.swing_id,
                        },
                    )
                )
                continue
            mss_index = index
            fvg = candidate_gap
            break
        if mss_index is None or fvg is None:
            diagnostics.append(
                EventDiagnostic(
                    event.event_id,
                    "MSS_FVG_NOT_CONFIRMED_WITHIN_WINDOW",
                    {"swing_id": swing.swing_id, "swing_level": swing.level},
                )
            )
            continue

        mss_row = work.iloc[mss_index]
        observed_index = mss_index
        retest_ns: int | None = None
        if require_fvg_retest:
            retest_end = min(
                len(work.index),
                mss_index + 1 + logic.maximum_retest_minutes,
            )
            retest_found = False
            invalidated = False
            for index in range(mss_index + 1, retest_end):
                row = work.iloc[index]
                if event.direction == "LONG":
                    touched = float(row["low"]) <= fvg.upper
                    full_traversal = float(row["low"]) <= fvg.lower
                    rejected = (
                        touched
                        and not full_traversal
                        and float(row["close"]) >= fvg.upper
                        and float(row["close"]) > float(row["open"])
                        and float(row["close_location"]) >= logic.retest_close_location
                    )
                    source_invalid = float(row["low"]) <= event.event_extreme
                else:
                    touched = float(row["high"]) >= fvg.lower
                    full_traversal = float(row["high"]) >= fvg.upper
                    rejected = (
                        touched
                        and not full_traversal
                        and float(row["close"]) <= fvg.lower
                        and float(row["close"]) < float(row["open"])
                        and float(row["close_location"])
                        <= 1.0 - logic.retest_close_location
                    )
                    source_invalid = float(row["high"]) >= event.event_extreme
                if source_invalid or full_traversal:
                    invalidated = True
                    diagnostics.append(
                        EventDiagnostic(
                            event.event_id,
                            "RETEST_INVALIDATED",
                            {
                                "timestamp_ns": int(row["timestamp_ns"]),
                                "source_invalid": source_invalid,
                                "full_fvg_traversal": full_traversal,
                            },
                        )
                    )
                    break
                if rejected:
                    observed_index = index
                    retest_ns = int(row["timestamp_ns"])
                    retest_found = True
                    break
            if invalidated:
                continue
            if not retest_found:
                diagnostics.append(
                    EventDiagnostic(
                        event.event_id,
                        "FIRST_FVG_RETEST_NOT_CONFIRMED",
                        {"fvg_id": fvg.gap_id},
                    )
                )
                continue

        entry_row = work.iloc[observed_index]
        entry = float(entry_row["close"])
        atr = float(entry_row["atr"])
        stop = (
            event.event_extreme - logic.stop_buffer_atr * atr
            if event.direction == "LONG"
            else event.event_extreme + logic.stop_buffer_atr * atr
        )
        risk = entry - stop if event.direction == "LONG" else stop - entry
        if not np.isfinite(risk) or risk <= 0.0:
            diagnostics.append(
                EventDiagnostic(
                    event.event_id,
                    "NONPOSITIVE_SOURCE_INVALIDATION",
                    {"entry": entry, "stop": stop},
                )
            )
            continue
        plan = EntryPlan(
            scenario_id=f"{event.event_id}-MSS-FVG",
            event_id=event.event_id,
            direction=event.direction,
            observed_ns=int(entry_row["timestamp_ns"]),
            entry=entry,
            stop=stop,
            source_level=event.source_level,
            source_pool_id=event.source_pool_id,
            mss_swing_id=swing.swing_id,
            mss_level=swing.level,
            mss_ns=int(mss_row["timestamp_ns"]),
            fvg_id=fvg.gap_id,
            fvg_lower=fvg.lower,
            fvg_upper=fvg.upper,
            retest_required=require_fvg_retest,
            retest_ns=retest_ns,
            body_atr=float(mss_row["body_atr"]),
            displacement_rank=float(mss_row["displacement_rank"]),
        )
        plans.append(plan)
        diagnostics.append(
            EventDiagnostic(
                event.event_id,
                "ENTRY_READY",
                {
                    "scenario_id": plan.scenario_id,
                    "observed_ns": plan.observed_ns,
                    "retest_required": require_fvg_retest,
                },
            )
        )
    return plans, diagnostics


__all__ = [
    "Direction",
    "EntryPlan",
    "EventDiagnostic",
    "FairValueGap",
    "ImpactEvent",
    "ImpactMSSFVGLogic",
    "Swing",
    "confirmed_swings",
    "diagnose",
    "minute_features",
]
