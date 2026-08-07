"""Cross-market liquidity-shock resilience state machine for candidate-02 v93.

The module separates an external-liquidity event from two mutually exclusive
trading scenarios:

* LOCAL_REVERSION: a frozen eight-hour boundary is swept, the auction closes
  back inside, opposite displacement breaks internal structure, and a later FVG
  retrace offers entry toward the nearest intact internal pivot liquidity.
* COMMON_BREAKOUT_CONTINUATION: perpetual and spot both accept beyond the
  boundary without excessive basis expansion, breakout displacement leaves an
  FVG, and its later retest holds outside the old range.  The objective is the
  nearest intact external pivot known before the event.

Targets are pre-existing confirmed pivots, not arbitrary reward multiples.
NautilusTrader exclusively owns orders, fills, fees, positions and account NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from v53_nt_core import CostConfig, RotationSignal, cost_after_reward_risk

UTC = "UTC"
NS_MINUTE = 60_000_000_000
CYCLE_HOURS = (0, 8, 16)
MODES = {"STATE_PORTFOLIO", "LOCAL_REVERSION", "COMMON_BREAKOUT_CONTINUATION"}


@dataclass(frozen=True, slots=True)
class LiquidityResilienceConfig:
    mode: str = "STATE_PORTFOLIO"
    dealing_range_hours: int = 8
    active_window_minutes: int = 240
    prior_window_minutes: int = 2880
    prior_minimum_minutes: int = 720
    turnover_quantile: float = 0.50
    sweep_breach_atr: float = 0.02
    maximum_sweep_extension_atr: float = 1.50
    classification_minutes: int = 3
    continuation_minimum_outside_closes: int = 2
    continuation_acceptance_atr: float = 0.03
    continuation_minimum_spot_ratio: float = 0.25
    continuation_maximum_basis_expansion_share: float = 0.75
    reclaim_depth_atr: float = 0.00
    displacement_minutes: int = 6
    displacement_body_quantile: float = 0.55
    minimum_displacement_body_atr: float = 0.20
    internal_structure_lookback_minutes: int = 5
    minimum_fvg_atr: float = 0.01
    retrace_minutes: int = 20
    minimum_retrace_flow_alignment: float = -0.15
    pivot_bar_minutes: int = 5
    pivot_radius_bars: int = 2
    internal_target_lookback_minutes: int = 480
    external_target_lookback_minutes: int = 2880
    minimum_target_cost_after_rr: float = 1.10
    maximum_target_cost_after_rr: float = 1000.0
    stop_buffer_atr: float = 0.05
    continuation_invalidation_atr: float = 0.10
    cooldown_minutes: int = 30
    maximum_holding_minutes: int = 180

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "LiquidityResilienceConfig":
        unknown = sorted(set(values) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"unknown v93 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown v93 mode: {self.mode}")
        if self.dealing_range_hours != 8:
            raise ValueError("v93 fixes completed eight-hour dealing cycles")
        if not 60 <= self.active_window_minutes <= 360:
            raise ValueError("invalid active window")
        if self.prior_window_minutes < 1440 or self.prior_minimum_minutes < 360:
            raise ValueError("insufficient shifted history")
        for name in ("turnover_quantile", "displacement_body_quantile"):
            value = float(getattr(self, name))
            if not 0.0 < value < 1.0:
                raise ValueError(f"invalid {name}")
        if not 0.0 <= self.sweep_breach_atr < self.maximum_sweep_extension_atr:
            raise ValueError("invalid sweep geometry")
        if self.classification_minutes not in {2, 3, 4, 5}:
            raise ValueError("invalid resilience-classification horizon")
        if not 1 <= self.continuation_minimum_outside_closes <= self.classification_minutes + 1:
            raise ValueError("invalid outside-close requirement")
        if self.continuation_acceptance_atr < 0.0:
            raise ValueError("negative acceptance distance")
        if not 0.0 <= self.continuation_minimum_spot_ratio <= 2.0:
            raise ValueError("invalid spot acceptance ratio")
        if not 0.0 <= self.continuation_maximum_basis_expansion_share <= 2.0:
            raise ValueError("invalid basis share")
        if self.displacement_minutes not in {3, 4, 5, 6, 7, 8}:
            raise ValueError("invalid displacement horizon")
        if self.internal_structure_lookback_minutes < 3:
            raise ValueError("internal structure lookback too short")
        if not 0.0 <= self.minimum_fvg_atr <= 0.25:
            raise ValueError("invalid FVG floor")
        if not 5 <= self.retrace_minutes <= 60:
            raise ValueError("invalid retrace horizon")
        if not -1.0 < self.minimum_retrace_flow_alignment < 1.0:
            raise ValueError("invalid retrace flow floor")
        if self.pivot_bar_minutes not in {3, 5, 10, 15} or self.pivot_radius_bars not in {1, 2, 3}:
            raise ValueError("invalid causal pivot definition")
        if self.internal_target_lookback_minutes < self.dealing_range_hours * 60:
            raise ValueError("internal target history shorter than the dealing range")
        if self.external_target_lookback_minutes < 1440:
            raise ValueError("external target history too short")
        if not 0.0 < self.minimum_target_cost_after_rr <= self.maximum_target_cost_after_rr:
            raise ValueError("invalid target reward/risk band")
        if self.stop_buffer_atr < 0.0 or self.continuation_invalidation_atr <= 0.0:
            raise ValueError("invalid invalidation buffer")
        if self.cooldown_minutes < 0 or self.maximum_holding_minutes <= 0:
            raise ValueError("invalid timing")


def _normalise_index(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy().sort_index()
    if result.index.tz is None:
        result.index = result.index.tz_localize(UTC)
    else:
        result.index = result.index.tz_convert(UTC)
    if result.index.has_duplicates or not result.index.is_monotonic_increasing:
        raise ValueError("timestamps must be unique and increasing")
    return result


def build_state(features: pd.DataFrame, config: LiquidityResilienceConfig) -> pd.DataFrame:
    required = {
        "close",
        "aggressive_total_quote_1m",
        "signed_flow_ratio_1m",
        "spot_open",
        "spot_high",
        "spot_low",
        "spot_close",
        "spot_signed_flow_ratio_1m",
        "perp_spot_log_basis",
    }
    missing = sorted(required - set(features.columns))
    if missing:
        raise ValueError(f"v93 missing completed-minute features: {missing}")
    x = _normalise_index(features)
    x["turnover_threshold"] = (
        x["aggressive_total_quote_1m"]
        .rolling(config.prior_window_minutes, min_periods=config.prior_minimum_minutes)
        .quantile(config.turnover_quantile)
        .shift(1)
    )
    return x


def _normalise_timestamp(value: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize(UTC) if ts.tzinfo is None else ts.tz_convert(UTC)


def _cycle_anchors(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    day = (start - pd.Timedelta(days=1)).normalize()
    last = (end + pd.Timedelta(days=1)).normalize()
    output: list[pd.Timestamp] = []
    while day <= last:
        for hour in CYCLE_HOURS:
            output.append(day + pd.Timedelta(hours=hour))
        day += pd.Timedelta(days=1)
    return output


def _true_range(raw: pd.DataFrame) -> pd.Series:
    previous_close = raw["close"].shift(1)
    return pd.concat(
        [
            raw["high"] - raw["low"],
            (raw["high"] - previous_close).abs(),
            (raw["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def _finite(row: pd.Series, names: Sequence[str]) -> bool:
    return all(math.isfinite(float(row[name])) for name in names)


def _completed_bars(
    raw: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    minutes: int,
) -> pd.DataFrame:
    view = raw.loc[(raw.index > start) & (raw.index <= end), ["open", "high", "low", "close"]]
    if view.empty:
        return view
    bars = view.resample(f"{minutes}min", label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    )
    return bars.dropna()


def _intact_pivots(bars: pd.DataFrame, radius: int) -> tuple[list[float], list[float]]:
    if len(bars) < 2 * radius + 3:
        return [], []
    highs = bars["high"].to_numpy(dtype=float)
    lows = bars["low"].to_numpy(dtype=float)
    closes = bars["close"].to_numpy(dtype=float)
    intact_highs: list[float] = []
    intact_lows: list[float] = []
    for i in range(radius, len(bars) - radius):
        high = highs[i]
        low = lows[i]
        left_high = float(np.max(highs[i - radius : i]))
        right_high = float(np.max(highs[i + 1 : i + radius + 1]))
        left_low = float(np.min(lows[i - radius : i]))
        right_low = float(np.min(lows[i + 1 : i + radius + 1]))
        confirmation = i + radius
        later_closes = closes[confirmation + 1 :]
        is_high = high >= left_high and high >= right_high and (high > left_high or high > right_high)
        is_low = low <= left_low and low <= right_low and (low < left_low or low < right_low)
        if is_high and not np.any(later_closes > high):
            intact_highs.append(float(high))
        if is_low and not np.any(later_closes < low):
            intact_lows.append(float(low))
    return sorted(set(intact_highs)), sorted(set(intact_lows))


def _select_target(
    *,
    levels: Sequence[float],
    side: str,
    entry: float,
    stop: float,
    costs: CostConfig,
    minimum_rr: float,
    maximum_rr: float,
) -> tuple[float, float] | None:
    if side == "BUY":
        candidates = sorted({float(level) for level in levels if float(level) > entry})
    else:
        candidates = sorted({float(level) for level in levels if float(level) < entry}, reverse=True)
    for target in candidates:
        geometry = stop < entry < target if side == "BUY" else target < entry < stop
        if not geometry:
            continue
        rr = cost_after_reward_risk(
            entry=entry,
            stop=stop,
            target=target,
            side=side,
            costs=costs,
        )
        if math.isfinite(rr) and minimum_rr <= rr <= maximum_rr:
            return target, rr
    return None


def _find_displacement(
    *,
    x: pd.DataFrame,
    start_position: int,
    end_position: int,
    direction: int,
    structure_level: float,
    config: LiquidityResilienceConfig,
) -> tuple[int, float, float] | None:
    for position in range(max(start_position + 1, 2), min(end_position, len(x) - 1) + 1):
        row = x.iloc[position]
        if not _finite(row, ("raw_open", "raw_high", "raw_low", "raw_close", "body", "body_threshold", "atr")):
            continue
        atr = float(row["atr"])
        body_floor = max(float(row["body_threshold"]), config.minimum_displacement_body_atr * atr)
        directional_body = direction * (float(row["raw_close"]) - float(row["raw_open"]))
        if directional_body <= 0.0 or float(row["body"]) < body_floor:
            continue
        structure_broken = (
            float(row["raw_close"]) > structure_level
            if direction > 0
            else float(row["raw_close"]) < structure_level
        )
        if not structure_broken:
            continue
        two_back = x.iloc[position - 2]
        minimum_gap = config.minimum_fvg_atr * atr
        if direction > 0:
            fvg_low = float(two_back["raw_high"])
            fvg_high = float(row["raw_low"])
        else:
            fvg_low = float(row["raw_high"])
            fvg_high = float(two_back["raw_low"])
        if fvg_high - fvg_low < minimum_gap:
            continue
        return position, fvg_low, fvg_high
    return None


def _append_signal(
    output: list[RotationSignal],
    *,
    observed: pd.Timestamp,
    scenario_id: str,
    side: str,
    entry: float,
    stop: float,
    target: float,
    rr: float,
    score: float,
    max_hold_minutes: int,
    source_open_ns: int,
    details: Mapping[str, Any],
) -> None:
    observed_ns = int(observed.value)
    output.append(
        RotationSignal(
            scenario_id=scenario_id,
            observed_time_ns=observed_ns,
            side=side,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            cost_after_reward_risk=rr,
            score=score,
            max_hold_minutes=max_hold_minutes,
            source_feature_open_time_ns=source_open_ns,
            source_feature_available_time_ns=observed_ns,
            source_max_market_time_ns=observed_ns,
            details=dict(details),
        )
    )


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: LiquidityResilienceConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = _normalise_timestamp(pd.Timestamp(evaluation_start))
    end = _normalise_timestamp(pd.Timestamp(evaluation_end))
    if end <= start:
        raise ValueError("evaluation end must be after start")
    raw_view = _normalise_index(raw[["open", "high", "low", "close"]])
    x = state.join(
        raw_view.rename(
            columns={"open": "raw_open", "high": "raw_high", "low": "raw_low", "close": "raw_close"}
        ),
        how="inner",
    )
    true_range = _true_range(raw_view)
    x["atr"] = true_range.rolling(60, min_periods=30).median().shift(1).reindex(x.index)
    x["body"] = (x["raw_close"] - x["raw_open"]).abs()
    x["body_threshold"] = (
        x["body"]
        .rolling(config.prior_window_minutes, min_periods=config.prior_minimum_minutes)
        .quantile(config.displacement_body_quantile)
        .shift(1)
    )
    if x.index.has_duplicates or not x.index.is_monotonic_increasing:
        raise ValueError("invalid v93 joined minute index")

    signals: list[RotationSignal] = []
    used_observed_times: set[int] = set()
    cooldown_until = -1
    required_event_fields = (
        "raw_open", "raw_high", "raw_low", "raw_close", "atr",
        "aggressive_total_quote_1m", "turnover_threshold",
        "spot_close", "perp_spot_log_basis",
    )

    for anchor in _cycle_anchors(start, end):
        active_end = anchor + pd.Timedelta(minutes=config.active_window_minutes)
        if active_end <= start or anchor >= end:
            continue
        range_start = anchor - pd.Timedelta(hours=config.dealing_range_hours)
        formation = raw_view.loc[(raw_view.index > range_start) & (raw_view.index <= anchor)]
        if len(formation) < config.dealing_range_hours * 60 - 2:
            continue
        range_high = float(formation["high"].max())
        range_low = float(formation["low"].min())
        range_width = range_high - range_low
        if not all(math.isfinite(v) for v in (range_high, range_low, range_width)) or range_width <= 0.0:
            continue

        internal_bars = _completed_bars(
            raw_view,
            start=anchor - pd.Timedelta(minutes=config.internal_target_lookback_minutes),
            end=anchor,
            minutes=config.pivot_bar_minutes,
        )
        internal_highs, internal_lows = _intact_pivots(internal_bars, config.pivot_radius_bars)
        active = x.loc[
            (x.index > anchor)
            & (x.index <= active_end)
            & (x.index >= start)
            & (x.index < end)
        ]
        if active.empty:
            continue
        consumed_boundaries: set[int] = set()

        for sweep_ts in active.index:
            sweep_position = int(x.index.get_loc(sweep_ts))
            sweep = x.iloc[sweep_position]
            if not _finite(sweep, required_event_fields):
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
            if sweep_direction in consumed_boundaries:
                continue
            extension = upper_excess if swept_upper else lower_excess
            if extension > config.maximum_sweep_extension_atr * atr:
                continue
            if float(sweep["aggressive_total_quote_1m"]) < float(sweep["turnover_threshold"]):
                continue
            consumed_boundaries.add(sweep_direction)

            if sweep_position < 1:
                continue
            previous = x.iloc[sweep_position - 1]
            if not _finite(previous, ("perp_spot_log_basis",)):
                continue
            pre_basis = float(previous["perp_spot_log_basis"])
            boundary = range_high if sweep_direction > 0 else range_low
            spot_boundary = boundary / math.exp(pre_basis)
            classification_end = min(
                sweep_position + config.classification_minutes,
                len(x) - 1,
            )
            segment = x.iloc[sweep_position : classification_end + 1]
            segment = segment.loc[(segment.index <= active_end) & (segment.index < end)]
            if len(segment) < config.classification_minutes:
                continue
            outside = (
                segment["raw_close"] > range_high
                if sweep_direction > 0
                else segment["raw_close"] < range_low
            )
            last = segment.iloc[-1]
            last_ts = pd.Timestamp(segment.index[-1])
            final_perp = float(last["raw_close"])
            final_spot = float(last["spot_close"])
            final_basis = float(last["perp_spot_log_basis"])
            final_inside = range_low <= final_perp <= range_high
            final_outside_distance = sweep_direction * (final_perp - boundary)
            spot_outside_distance = sweep_direction * (final_spot - spot_boundary)
            perp_excess_fraction = max(
                sweep_direction * (final_perp / boundary - 1.0),
                1e-12,
            )
            spot_excess_fraction = sweep_direction * (final_spot / spot_boundary - 1.0)
            spot_ratio = spot_excess_fraction / perp_excess_fraction
            basis_expansion_share = max(
                sweep_direction * (final_basis - pre_basis),
                0.0,
            ) / perp_excess_fraction
            continuation = (
                int(outside.sum()) >= config.continuation_minimum_outside_closes
                and not final_inside
                and final_outside_distance >= config.continuation_acceptance_atr * atr
                and spot_outside_distance > 0.0
                and spot_ratio >= config.continuation_minimum_spot_ratio
                and basis_expansion_share <= config.continuation_maximum_basis_expansion_share
            )
            inside_positions = [
                sweep_position + offset
                for offset, value in enumerate(
                    ((segment["raw_close"] >= range_low) & (segment["raw_close"] <= range_high)).to_numpy()
                )
                if bool(value)
            ]
            reclaim_position = inside_positions[0] if inside_positions else None
            reversion = reclaim_position is not None and final_inside
            if continuation == reversion:
                continue

            internal = x.loc[
                (x.index >= sweep_ts - pd.Timedelta(minutes=config.internal_structure_lookback_minutes))
                & (x.index < sweep_ts)
            ]
            if len(internal) < config.internal_structure_lookback_minutes - 1:
                continue
            internal_high = float(internal["raw_high"].max())
            internal_low = float(internal["raw_low"].min())
            sweep_extreme = (
                float(segment["raw_high"].max())
                if sweep_direction > 0
                else float(segment["raw_low"].min())
            )

            if reversion:
                if config.mode not in {"STATE_PORTFOLIO", "LOCAL_REVERSION"}:
                    continue
                trade_direction = -sweep_direction
                structure_level = internal_low if trade_direction < 0 else internal_high
                displacement_start = int(reclaim_position)
                state_name = "LOCAL_REVERSION"
            else:
                if config.mode not in {"STATE_PORTFOLIO", "COMMON_BREAKOUT_CONTINUATION"}:
                    continue
                trade_direction = sweep_direction
                structure_level = boundary
                displacement_start = sweep_position - 1
                state_name = "COMMON_BREAKOUT_CONTINUATION"

            displacement = _find_displacement(
                x=x,
                start_position=displacement_start,
                end_position=min(displacement_start + config.displacement_minutes, len(x) - 1),
                direction=trade_direction,
                structure_level=structure_level,
                config=config,
            )
            if displacement is None:
                continue
            displacement_position, fvg_low, fvg_high = displacement
            displacement_row = x.iloc[displacement_position]
            displacement_ts = pd.Timestamp(x.index[displacement_position])
            retrace_end = min(displacement_position + config.retrace_minutes, len(x) - 1)

            for position in range(displacement_position + 1, retrace_end + 1):
                observed = pd.Timestamp(x.index[position])
                if observed > active_end or observed >= end:
                    break
                row = x.iloc[position]
                if not _finite(row, ("raw_high", "raw_low", "raw_close", "signed_flow_ratio_1m", "atr")):
                    continue
                if reversion:
                    invalidated = (
                        float(row["raw_high"]) >= sweep_extreme
                        if trade_direction < 0
                        else float(row["raw_low"]) <= sweep_extreme
                    )
                else:
                    invalidation_level = (
                        boundary - config.continuation_invalidation_atr * float(row["atr"])
                        if trade_direction > 0
                        else boundary + config.continuation_invalidation_atr * float(row["atr"])
                    )
                    invalidated = (
                        float(row["raw_low"]) <= invalidation_level
                        if trade_direction > 0
                        else float(row["raw_high"]) >= invalidation_level
                    )
                if invalidated:
                    break
                touched = float(row["raw_high"]) >= fvg_low and float(row["raw_low"]) <= fvg_high
                if not touched:
                    continue
                midpoint = 0.5 * (fvg_low + fvg_high)
                rejected = (
                    float(row["raw_close"]) >= midpoint
                    if trade_direction > 0
                    else float(row["raw_close"]) <= midpoint
                )
                if not rejected:
                    continue
                if continuation:
                    held_outside = (
                        float(row["raw_close"]) > range_high
                        if trade_direction > 0
                        else float(row["raw_close"]) < range_low
                    )
                    if not held_outside:
                        continue
                retrace_flow = trade_direction * float(row["signed_flow_ratio_1m"])
                if retrace_flow < config.minimum_retrace_flow_alignment:
                    continue
                observed_ns = int(observed.value)
                if observed_ns <= cooldown_until or observed_ns in used_observed_times:
                    continue

                entry = float(row["raw_close"])
                side = "BUY" if trade_direction > 0 else "SELL"
                if reversion:
                    stop = sweep_extreme + sweep_direction * config.stop_buffer_atr * float(row["atr"])
                    levels = (
                        [level for level in internal_highs if level <= range_high]
                        if side == "BUY"
                        else [level for level in internal_lows if level >= range_low]
                    )
                else:
                    stop = (
                        boundary - config.continuation_invalidation_atr * float(row["atr"])
                        if side == "BUY"
                        else boundary + config.continuation_invalidation_atr * float(row["atr"])
                    )
                    external_bars = _completed_bars(
                        raw_view,
                        start=sweep_ts - pd.Timedelta(minutes=config.external_target_lookback_minutes),
                        end=sweep_ts - pd.Timedelta(minutes=1),
                        minutes=config.pivot_bar_minutes,
                    )
                    external_highs, external_lows = _intact_pivots(external_bars, config.pivot_radius_bars)
                    levels = (
                        [level for level in external_highs if level > range_high]
                        if side == "BUY"
                        else [level for level in external_lows if level < range_low]
                    )
                selected = _select_target(
                    levels=levels,
                    side=side,
                    entry=entry,
                    stop=stop,
                    costs=costs,
                    minimum_rr=config.minimum_target_cost_after_rr,
                    maximum_rr=config.maximum_target_cost_after_rr,
                )
                if selected is None:
                    continue
                target, rr = selected
                displacement_strength = float(displacement_row["body"]) / max(
                    float(displacement_row["atr"]), 1e-12
                )
                turnover_ratio = float(sweep["aggressive_total_quote_1m"]) / max(
                    float(sweep["turnover_threshold"]), 1e-12
                )
                state_quality = (
                    max(spot_ratio, 0.0) / (1.0 + max(basis_expansion_share, 0.0))
                    if continuation
                    else 1.0 + max(-spot_ratio, 0.0) + max(basis_expansion_share, 0.0)
                )
                score = rr * max(displacement_strength, 0.0) * max(turnover_ratio, 1.0) * max(state_quality, 0.1)
                details = {
                    "state": state_name,
                    "cycle": f"CYCLE_{anchor.hour:02d}",
                    "cycle_anchor_utc": anchor.isoformat(),
                    "dealing_range_start_utc": range_start.isoformat(),
                    "dealing_range_end_utc": anchor.isoformat(),
                    "dealing_range_high": range_high,
                    "dealing_range_low": range_low,
                    "sweep_side": "BUY_SIDE_LIQUIDITY" if sweep_direction > 0 else "SELL_SIDE_LIQUIDITY",
                    "sweep_close_utc": sweep_ts.isoformat(),
                    "sweep_extreme": sweep_extreme,
                    "sweep_extension_atr": extension / atr,
                    "classification_close_utc": last_ts.isoformat(),
                    "outside_close_count": int(outside.sum()),
                    "spot_boundary": spot_boundary,
                    "spot_acceptance_ratio": spot_ratio,
                    "basis_expansion_share": basis_expansion_share,
                    "reclaim_close_utc": x.index[reclaim_position].isoformat() if reclaim_position is not None else None,
                    "internal_structure_high": internal_high,
                    "internal_structure_low": internal_low,
                    "displacement_close_utc": displacement_ts.isoformat(),
                    "displacement_body_atr": displacement_strength,
                    "fvg_low": fvg_low,
                    "fvg_high": fvg_high,
                    "fvg_midpoint": midpoint,
                    "retrace_close_utc": observed.isoformat(),
                    "retrace_flow_alignment": retrace_flow,
                    "selected_pivot_target": target,
                    "selected_target_cost_after_rr": rr,
                    "causal_interpretation": (
                        "failed external auction rotating only to the nearest intact internal liquidity"
                        if reversion
                        else "common spot-perpetual acceptance retesting the old boundary before the next intact external liquidity"
                    ),
                }
                _append_signal(
                    signals,
                    observed=observed,
                    scenario_id=f"v93-{state_name.lower()}-{observed_ns}",
                    side=side,
                    entry=entry,
                    stop=stop,
                    target=target,
                    rr=rr,
                    score=float(score),
                    max_hold_minutes=config.maximum_holding_minutes,
                    source_open_ns=int(sweep_ts.value) - NS_MINUTE,
                    details=details,
                )
                used_observed_times.add(observed_ns)
                cooldown_until = observed_ns + config.cooldown_minutes * NS_MINUTE
                break

    signals.sort(key=lambda signal: (signal.observed_time_ns, -signal.score, signal.scenario_id))
    unique: list[RotationSignal] = []
    seen: set[int] = set()
    for signal in signals:
        if signal.observed_time_ns in seen:
            continue
        seen.add(signal.observed_time_ns)
        if signal.source_max_market_time_ns > signal.observed_time_ns:
            raise AssertionError("future information detected in v93")
        unique.append(signal)
    return unique


__all__ = ["LiquidityResilienceConfig", "build_state", "build_rotation_signals"]
