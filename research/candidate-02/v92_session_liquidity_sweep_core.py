"""Session-liquidity sweep, displacement and FVG-retrace state machine.

This module implements a causal SMC/ICT scenario rather than a candle-pattern
classifier:

1. Freeze the completed eight-hour dealing range before each 00:00, 08:00 and
   16:00 UTC cycle.
2. Observe an external-liquidity sweep beyond one range boundary with
   aggressive flow in the sweep direction.
3. Require a prompt close back inside the frozen range (failed auction).
4. Require opposite displacement through a pre-sweep internal structure level
   and a three-candle fair-value gap.
5. Enter only on a later completed-minute retrace into that imbalance.
6. Invalidate beyond the sweep extreme and target the opposite external
   liquidity boundary.

All adaptive thresholds are shifted and use prior completed minutes only.
NautilusTrader exclusively owns orders, fills, fees, positions and account NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from v53_nt_core import CostConfig, RotationSignal, cost_after_reward_risk

UTC = "UTC"
NS_MINUTE = 60_000_000_000
MODES = {"STATE_PORTFOLIO", "CYCLE_00", "CYCLE_08", "CYCLE_16"}
CYCLE_HOURS = (0, 8, 16)


@dataclass(frozen=True, slots=True)
class SessionLiquiditySweepConfig:
    mode: str = "STATE_PORTFOLIO"
    dealing_range_hours: int = 8
    active_window_minutes: int = 240
    prior_window_minutes: int = 2880
    prior_minimum_minutes: int = 720
    flow_abs_quantile: float = 0.55
    turnover_quantile: float = 0.50
    minimum_sweep_flow_alignment: float = 0.08
    sweep_breach_atr: float = 0.03
    maximum_sweep_extension_atr: float = 1.25
    reclaim_minutes: int = 3
    reclaim_depth_atr: float = 0.00
    displacement_minutes: int = 5
    displacement_body_quantile: float = 0.60
    minimum_displacement_body_atr: float = 0.25
    internal_structure_lookback_minutes: int = 5
    minimum_fvg_atr: float = 0.02
    retrace_minutes: int = 20
    minimum_retrace_flow_alignment: float = -0.10
    atr_lookback_minutes: int = 60
    stop_buffer_atr: float = 0.05
    target_boundary_fraction: float = 1.00
    cooldown_minutes: int = 30
    maximum_holding_minutes: int = 240
    minimum_cost_after_rr: float = 1.00
    maximum_cost_after_rr: float = 8.00

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "SessionLiquiditySweepConfig":
        unknown = sorted(set(values) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"unknown v92 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown v92 mode: {self.mode}")
        if self.dealing_range_hours != 8:
            raise ValueError("v92 prospectively fixes an eight-hour dealing range")
        if not 60 <= self.active_window_minutes <= 360:
            raise ValueError("invalid active window")
        if self.prior_window_minutes < 1440 or self.prior_minimum_minutes < 360:
            raise ValueError("insufficient prior completed-minute history")
        for name in ("flow_abs_quantile", "turnover_quantile", "displacement_body_quantile"):
            value = float(getattr(self, name))
            if not 0.0 < value < 1.0:
                raise ValueError(f"invalid {name}")
        if not 0.0 <= self.minimum_sweep_flow_alignment < 1.0:
            raise ValueError("invalid sweep-flow floor")
        if not 0.0 <= self.sweep_breach_atr < self.maximum_sweep_extension_atr:
            raise ValueError("invalid sweep geometry")
        if self.reclaim_minutes not in {1, 2, 3, 4, 5}:
            raise ValueError("reclaim window outside structural range")
        if self.displacement_minutes not in {2, 3, 4, 5, 6}:
            raise ValueError("displacement window outside structural range")
        if self.internal_structure_lookback_minutes < 3:
            raise ValueError("internal structure lookback too short")
        if not 0.0 <= self.minimum_fvg_atr <= 0.25:
            raise ValueError("invalid FVG floor")
        if not 5 <= self.retrace_minutes <= 60:
            raise ValueError("invalid retrace window")
        if not -1.0 < self.minimum_retrace_flow_alignment < 1.0:
            raise ValueError("invalid retrace-flow floor")
        if self.atr_lookback_minutes < 30:
            raise ValueError("ATR history too short")
        if self.stop_buffer_atr < 0.0:
            raise ValueError("negative stop buffer")
        if not 0.50 <= self.target_boundary_fraction <= 1.00:
            raise ValueError("target must remain inside or at opposite external liquidity")
        if self.cooldown_minutes < 0 or self.maximum_holding_minutes <= 0:
            raise ValueError("invalid timing")
        if not 0.0 < self.minimum_cost_after_rr <= self.maximum_cost_after_rr:
            raise ValueError("invalid cost-after reward/risk band")


def build_state(features: pd.DataFrame, config: SessionLiquiditySweepConfig) -> pd.DataFrame:
    required = {
        "close",
        "aggressive_total_quote_1m",
        "aggressive_signed_quote_1m",
        "signed_flow_ratio_1m",
    }
    missing = sorted(required - set(features.columns))
    if missing:
        raise ValueError(f"v92 missing completed-minute features: {missing}")

    x = features.copy().sort_index()
    if x.index.tz is None:
        x.index = x.index.tz_localize(UTC)
    else:
        x.index = x.index.tz_convert(UTC)
    if x.index.has_duplicates:
        raise ValueError("duplicate v92 feature timestamps")

    prior = config.prior_window_minutes
    minimum = config.prior_minimum_minutes
    x["flow_abs_threshold"] = (
        x["signed_flow_ratio_1m"].abs()
        .rolling(prior, min_periods=minimum)
        .quantile(config.flow_abs_quantile)
        .shift(1)
    )
    x["turnover_threshold"] = (
        x["aggressive_total_quote_1m"]
        .rolling(prior, min_periods=minimum)
        .quantile(config.turnover_quantile)
        .shift(1)
    )
    return x


def _normalise_timestamp(value: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize(UTC) if ts.tzinfo is None else ts.tz_convert(UTC)


def _cycle_label(anchor: pd.Timestamp) -> str:
    return f"CYCLE_{anchor.hour:02d}"


def _cycle_anchors(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    first_day = (start - pd.Timedelta(days=1)).normalize()
    last_day = (end + pd.Timedelta(days=1)).normalize()
    anchors: list[pd.Timestamp] = []
    day = first_day
    while day <= last_day:
        for hour in CYCLE_HOURS:
            anchors.append(day + pd.Timedelta(hours=hour))
        day += pd.Timedelta(days=1)
    return anchors


def _finite(row: pd.Series, names: tuple[str, ...]) -> bool:
    return all(math.isfinite(float(row[name])) for name in names)


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: SessionLiquiditySweepConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = _normalise_timestamp(pd.Timestamp(evaluation_start))
    end = _normalise_timestamp(pd.Timestamp(evaluation_end))
    if end <= start:
        raise ValueError("evaluation end must be after start")

    raw_view = raw[["open", "high", "low", "close"]].copy().sort_index()
    if raw_view.index.tz is None:
        raw_view.index = raw_view.index.tz_localize(UTC)
    else:
        raw_view.index = raw_view.index.tz_convert(UTC)
    x = state.join(
        raw_view.rename(
            columns={
                "open": "raw_open",
                "high": "raw_high",
                "low": "raw_low",
                "close": "raw_close",
            }
        ),
        how="inner",
    )
    if x.index.has_duplicates or not x.index.is_monotonic_increasing:
        raise ValueError("invalid v92 joined minute index")

    previous_close = x["raw_close"].shift(1)
    true_range = pd.concat(
        [
            x["raw_high"] - x["raw_low"],
            (x["raw_high"] - previous_close).abs(),
            (x["raw_low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    x["atr"] = true_range.rolling(
        config.atr_lookback_minutes,
        min_periods=max(30, config.atr_lookback_minutes // 2),
    ).median().shift(1)
    x["body"] = (x["raw_close"] - x["raw_open"]).abs()
    x["body_threshold"] = (
        x["body"]
        .rolling(config.prior_window_minutes, min_periods=config.prior_minimum_minutes)
        .quantile(config.displacement_body_quantile)
        .shift(1)
    )

    fields = (
        "raw_open",
        "raw_high",
        "raw_low",
        "raw_close",
        "signed_flow_ratio_1m",
        "aggressive_total_quote_1m",
        "flow_abs_threshold",
        "turnover_threshold",
        "atr",
        "body",
        "body_threshold",
    )
    signals: list[RotationSignal] = []
    cooldown_until = -1
    used_observed_times: set[int] = set()

    for anchor in _cycle_anchors(start, end):
        cycle = _cycle_label(anchor)
        if config.mode != "STATE_PORTFOLIO" and config.mode != cycle:
            continue
        active_start = anchor
        active_end = anchor + pd.Timedelta(minutes=config.active_window_minutes)
        if active_end <= start or active_start >= end:
            continue

        range_start = anchor - pd.Timedelta(hours=config.dealing_range_hours)
        formation = x.loc[(x.index > range_start) & (x.index <= anchor)]
        if len(formation) < config.dealing_range_hours * 60 - 2:
            continue
        range_high = float(formation["raw_high"].max())
        range_low = float(formation["raw_low"].min())
        range_width = range_high - range_low
        if not all(math.isfinite(v) for v in (range_high, range_low, range_width)) or range_width <= 0.0:
            continue

        active = x.loc[
            (x.index > active_start)
            & (x.index <= active_end)
            & (x.index >= start)
            & (x.index < end)
        ]
        if active.empty:
            continue
        active_positions = [x.index.get_loc(ts) for ts in active.index]
        cycle_completed = False

        for sweep_position in active_positions:
            if cycle_completed:
                break
            sweep_ts = x.index[sweep_position]
            sweep = x.iloc[sweep_position]
            observed_candidate_ns = int(sweep_ts.value)
            if observed_candidate_ns <= cooldown_until or not _finite(sweep, fields):
                continue
            atr = float(sweep["atr"])
            if atr <= 0.0:
                continue

            upper_excess = float(sweep["raw_high"]) - range_high
            lower_excess = range_low - float(sweep["raw_low"])
            swept_upper = upper_excess >= config.sweep_breach_atr * atr
            swept_lower = lower_excess >= config.sweep_breach_atr * atr
            if swept_upper == swept_lower:
                continue
            sweep_direction = 1 if swept_upper else -1
            extension = upper_excess if swept_upper else lower_excess
            if extension > config.maximum_sweep_extension_atr * atr:
                continue

            flow_alignment = sweep_direction * float(sweep["signed_flow_ratio_1m"])
            flow_floor = max(
                config.minimum_sweep_flow_alignment,
                float(sweep["flow_abs_threshold"]),
            )
            if flow_alignment < flow_floor:
                continue
            if float(sweep["aggressive_total_quote_1m"]) < float(sweep["turnover_threshold"]):
                continue

            lookback_start = sweep_ts - pd.Timedelta(
                minutes=config.internal_structure_lookback_minutes
            )
            internal = x.loc[(x.index >= lookback_start) & (x.index < sweep_ts)]
            if len(internal) < config.internal_structure_lookback_minutes - 1:
                continue
            internal_high = float(internal["raw_high"].max())
            internal_low = float(internal["raw_low"].min())
            sweep_extreme = (
                float(sweep["raw_high"]) if sweep_direction > 0 else float(sweep["raw_low"])
            )

            reclaim_position = None
            reclaim_limit = min(
                sweep_position + config.reclaim_minutes,
                len(x) - 1,
            )
            for position in range(sweep_position, reclaim_limit + 1):
                ts = x.index[position]
                if ts > active_end or ts >= end:
                    break
                row = x.iloc[position]
                if not _finite(row, ("raw_close", "raw_high", "raw_low")):
                    continue
                if sweep_direction > 0:
                    sweep_extreme = max(sweep_extreme, float(row["raw_high"]))
                    reclaimed = float(row["raw_close"]) <= (
                        range_high - config.reclaim_depth_atr * atr
                    )
                else:
                    sweep_extreme = min(sweep_extreme, float(row["raw_low"]))
                    reclaimed = float(row["raw_close"]) >= (
                        range_low + config.reclaim_depth_atr * atr
                    )
                if reclaimed:
                    reclaim_position = position
                    break
            if reclaim_position is None:
                continue

            trade_direction = -sweep_direction
            displacement_position = None
            fvg_low = math.nan
            fvg_high = math.nan
            displacement_limit = min(
                reclaim_position + config.displacement_minutes,
                len(x) - 1,
            )
            for position in range(reclaim_position + 1, displacement_limit + 1):
                ts = x.index[position]
                if ts > active_end or ts >= end or position < 2:
                    break
                row = x.iloc[position]
                if not _finite(
                    row,
                    ("raw_open", "raw_high", "raw_low", "raw_close", "body", "body_threshold", "atr"),
                ):
                    continue
                local_atr = float(row["atr"])
                body_floor = max(
                    float(row["body_threshold"]),
                    config.minimum_displacement_body_atr * local_atr,
                )
                directional_body = trade_direction * (
                    float(row["raw_close"]) - float(row["raw_open"])
                )
                if directional_body <= 0.0 or float(row["body"]) < body_floor:
                    continue
                structure_broken = (
                    float(row["raw_close"]) < internal_low
                    if trade_direction < 0
                    else float(row["raw_close"]) > internal_high
                )
                if not structure_broken:
                    continue

                two_back = x.iloc[position - 2]
                minimum_gap = config.minimum_fvg_atr * local_atr
                if trade_direction > 0:
                    lower = float(two_back["raw_high"])
                    upper = float(row["raw_low"])
                    valid_fvg = upper - lower >= minimum_gap
                else:
                    lower = float(row["raw_high"])
                    upper = float(two_back["raw_low"])
                    valid_fvg = upper - lower >= minimum_gap
                if not valid_fvg:
                    continue
                displacement_position = position
                fvg_low = lower
                fvg_high = upper
                break
            if displacement_position is None:
                continue

            displacement = x.iloc[displacement_position]
            displacement_ts = x.index[displacement_position]
            retrace_limit = min(
                displacement_position + config.retrace_minutes,
                len(x) - 1,
            )
            for position in range(displacement_position + 1, retrace_limit + 1):
                ts = x.index[position]
                if ts > active_end or ts >= end:
                    break
                row = x.iloc[position]
                if not _finite(
                    row,
                    ("raw_high", "raw_low", "raw_close", "signed_flow_ratio_1m", "atr"),
                ):
                    continue
                if trade_direction < 0 and float(row["raw_high"]) >= sweep_extreme:
                    break
                if trade_direction > 0 and float(row["raw_low"]) <= sweep_extreme:
                    break

                touched = (
                    float(row["raw_high"]) >= fvg_low
                    and float(row["raw_low"]) <= fvg_high
                )
                if not touched:
                    continue
                midpoint = 0.5 * (fvg_low + fvg_high)
                rejection = (
                    float(row["raw_close"]) <= midpoint
                    if trade_direction < 0
                    else float(row["raw_close"]) >= midpoint
                )
                if not rejection:
                    continue
                retrace_flow = trade_direction * float(row["signed_flow_ratio_1m"])
                if retrace_flow < config.minimum_retrace_flow_alignment:
                    continue

                observed_ns = int(ts.value)
                if observed_ns in used_observed_times or observed_ns <= cooldown_until:
                    continue
                entry = float(row["raw_close"])
                stop = sweep_extreme + (
                    sweep_direction * config.stop_buffer_atr * float(row["atr"])
                )
                opposite_boundary = range_low if trade_direction < 0 else range_high
                target = entry + config.target_boundary_fraction * (
                    opposite_boundary - entry
                )
                geometry = (
                    target < entry < stop
                    if trade_direction < 0
                    else stop < entry < target
                )
                if not geometry:
                    continue
                side = "SELL" if trade_direction < 0 else "BUY"
                rr = cost_after_reward_risk(
                    entry=entry,
                    stop=stop,
                    target=target,
                    side=side,
                    costs=costs,
                )
                if not math.isfinite(rr) or not (
                    config.minimum_cost_after_rr
                    <= rr
                    <= config.maximum_cost_after_rr
                ):
                    continue

                turnover_ratio = float(sweep["aggressive_total_quote_1m"]) / max(
                    float(sweep["turnover_threshold"]),
                    1e-12,
                )
                displacement_strength = float(displacement["body"]) / max(
                    float(displacement["atr"]),
                    1e-12,
                )
                score = (
                    rr
                    * max(flow_alignment, 0.0)
                    * max(turnover_ratio, 1.0)
                    * max(displacement_strength, 0.0)
                )
                details = {
                    "state": "FAILED_EXTERNAL_SWEEP_DISPLACEMENT_FVG_RETRACE",
                    "cycle": cycle,
                    "cycle_anchor_utc": anchor.isoformat(),
                    "dealing_range_start_utc": range_start.isoformat(),
                    "dealing_range_end_utc": anchor.isoformat(),
                    "dealing_range_high": range_high,
                    "dealing_range_low": range_low,
                    "dealing_range_width": range_width,
                    "sweep_side": "BUY_SIDE_LIQUIDITY" if sweep_direction > 0 else "SELL_SIDE_LIQUIDITY",
                    "sweep_close_utc": sweep_ts.isoformat(),
                    "sweep_extreme": sweep_extreme,
                    "sweep_extension_atr": extension / atr,
                    "sweep_flow_alignment": flow_alignment,
                    "sweep_flow_floor": flow_floor,
                    "reclaim_close_utc": x.index[reclaim_position].isoformat(),
                    "internal_structure_high": internal_high,
                    "internal_structure_low": internal_low,
                    "displacement_close_utc": displacement_ts.isoformat(),
                    "displacement_body_atr": displacement_strength,
                    "choch_direction": "BEARISH" if trade_direction < 0 else "BULLISH",
                    "fvg_low": fvg_low,
                    "fvg_high": fvg_high,
                    "fvg_midpoint": midpoint,
                    "retrace_close_utc": ts.isoformat(),
                    "retrace_flow_alignment": retrace_flow,
                    "target_external_liquidity": (
                        "SELL_SIDE_RANGE_LOW" if trade_direction < 0 else "BUY_SIDE_RANGE_HIGH"
                    ),
                    "entry_order_type": "MARKET_AFTER_COMPLETED_RETRACE_MINUTE",
                    "causal_interpretation": (
                        "external stop liquidity was consumed, the auction failed, "
                        "opposite displacement broke internal structure, and the "
                        "imbalance retrace offered entry toward the opposing pool"
                    ),
                }
                signals.append(
                    RotationSignal(
                        scenario_id=f"v92-{cycle.lower()}-{observed_ns}",
                        observed_time_ns=observed_ns,
                        side=side,
                        entry_reference=entry,
                        stop_price=stop,
                        target_price=target,
                        cost_after_reward_risk=rr,
                        score=float(score),
                        max_hold_minutes=config.maximum_holding_minutes,
                        source_feature_open_time_ns=int(sweep_ts.value) - NS_MINUTE,
                        source_feature_available_time_ns=observed_ns,
                        source_max_market_time_ns=observed_ns,
                        details=details,
                    )
                )
                used_observed_times.add(observed_ns)
                cooldown_until = observed_ns + config.cooldown_minutes * NS_MINUTE
                cycle_completed = True
                break

    signals.sort(key=lambda signal: (signal.observed_time_ns, -signal.score, signal.scenario_id))
    for signal in signals:
        if signal.source_max_market_time_ns > signal.observed_time_ns:
            raise AssertionError("future information detected in v92")
    return signals


__all__ = [
    "SessionLiquiditySweepConfig",
    "build_state",
    "build_rotation_signals",
]
