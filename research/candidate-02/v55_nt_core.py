"""Frozen-auction acceptance/rejection signals for candidate-02 v55.

The module creates causal trade intents only. NautilusTrader remains the sole
engine for orders, fills, positions, fees and NAV.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from v53_nt_core import (
    CostConfig,
    RotationSignal,
    cost_after_reward_risk,
)

NS_MINUTE = 60_000_000_000


@dataclass(frozen=True, slots=True)
class FrozenAuctionConfig:
    formation_bars_5m: int = 12
    prior_quantile_days: int = 30
    prior_quantile_min_rows: int = 1000
    efficiency_quantile: float = 0.45
    vpin_quantile: float = 0.55
    oi_abs_quantile: float = 0.70
    atr_lookback_minutes: int = 60
    minimum_boundary_excursion_atr: float = 0.10
    minimum_outward_flow: float = 0.0
    maximum_classification_bars: int = 3
    rejection_reclaim_depth_atr: float = 0.25
    rejection_inward_depth_min: float = 0.0
    rejection_inward_flow_min: float = -0.10
    rejection_stop_buffer_atr: float = 0.15
    rejection_maximum_holding_minutes: int = 180
    acceptance_consecutive_closes: int = 2
    acceptance_outward_flow_min: float = 0.0
    acceptance_outward_depth_min: float = -0.10
    acceptance_stop_inside_atr: float = 0.15
    acceptance_target_range_extension: float = 0.75
    acceptance_maximum_holding_minutes: int = 120
    cooldown_bars_after_decision: int = 6
    minimum_cost_after_rr: float = 1.0
    maximum_cost_after_rr: float = 5.0

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "FrozenAuctionConfig":
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"unknown v55 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        if self.formation_bars_5m < 6:
            raise ValueError("formation_bars_5m must be at least 6")
        if self.prior_quantile_days <= 0 or self.prior_quantile_min_rows <= 0:
            raise ValueError("prior quantile history must be positive")
        for name in ("efficiency_quantile", "vpin_quantile", "oi_abs_quantile"):
            value = getattr(self, name)
            if not 0 < value < 1:
                raise ValueError(f"{name} must be in (0, 1)")
        if self.atr_lookback_minutes < 30:
            raise ValueError("atr lookback must be at least 30 minutes")
        if self.minimum_boundary_excursion_atr < 0:
            raise ValueError("minimum excursion cannot be negative")
        if self.maximum_classification_bars <= 0:
            raise ValueError("classification horizon must be positive")
        if self.acceptance_consecutive_closes < 2:
            raise ValueError("acceptance requires at least two closes")
        if not 0 < self.minimum_cost_after_rr <= self.maximum_cost_after_rr:
            raise ValueError("invalid cost-after reward/risk bounds")


def _prior_quantile(
    series: pd.Series,
    *,
    days: int,
    quantile: float,
    minimum_rows: int,
) -> pd.Series:
    return series.shift(1).rolling(days * 288, min_periods=minimum_rows).quantile(quantile)


def build_state(features: pd.DataFrame, config: FrozenAuctionConfig) -> pd.DataFrame:
    x = features.copy()
    n = config.formation_bars_5m
    previous_close = x["close"].shift(1)
    formation_start_close = x["close"].shift(n)
    formation_path = x["close"].diff().abs().shift(1).rolling(
        n - 1,
        min_periods=n - 1,
    ).sum()
    x["formation_efficiency"] = (
        (previous_close - formation_start_close).abs()
        / formation_path.replace(0.0, np.nan)
    )
    x["formation_vpin"] = x["vpin_50"].shift(1)
    x["formation_abs_oi_change"] = x["oi_change_1h"].shift(1).abs()
    x["efficiency_threshold"] = _prior_quantile(
        x["formation_efficiency"],
        days=config.prior_quantile_days,
        quantile=config.efficiency_quantile,
        minimum_rows=config.prior_quantile_min_rows,
    )
    x["vpin_threshold"] = _prior_quantile(
        x["vpin_50"],
        days=config.prior_quantile_days,
        quantile=config.vpin_quantile,
        minimum_rows=config.prior_quantile_min_rows,
    )
    x["oi_abs_threshold"] = _prior_quantile(
        x["oi_change_1h"].abs(),
        days=config.prior_quantile_days,
        quantile=config.oi_abs_quantile,
        minimum_rows=config.prior_quantile_min_rows,
    )
    x["balanced_auction"] = (
        (x["formation_efficiency"] <= x["efficiency_threshold"])
        & (x["formation_vpin"] <= x["vpin_threshold"])
        & (x["formation_abs_oi_change"] <= x["oi_abs_threshold"])
    )
    return x


def _aggregate_raw_five_minute(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        raise ValueError("raw one-minute data are empty")
    bucket = (raw.index - pd.Timedelta(nanoseconds=1)).floor("5min")
    frame = raw.copy()
    frame["bucket_open"] = bucket
    bars = frame.groupby("bucket_open", sort=True).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        minute_count=("close", "size"),
    )
    bars.index = pd.DatetimeIndex(bars.index, tz="UTC")
    bars.index.name = "bar_open_utc"
    return bars.loc[bars["minute_count"] == 5].copy()


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


def _utc(value: pd.Timestamp) -> pd.Timestamp:
    result = pd.Timestamp(value)
    return result.tz_localize("UTC") if result.tzinfo is None else result.tz_convert("UTC")


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: FrozenAuctionConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = _utc(evaluation_start)
    end = _utc(evaluation_end)
    if end <= start:
        raise ValueError("evaluation_end must be after evaluation_start")

    five = _aggregate_raw_five_minute(raw)
    aligned = state.join(
        five[["open", "high", "low", "close", "minute_count"]].rename(
            columns={
                "open": "raw_open_5m",
                "high": "raw_high_5m",
                "low": "raw_low_5m",
                "close": "raw_close_5m",
            }
        ),
        how="inner",
    )
    if aligned.empty:
        raise ValueError("no aligned feature and raw five-minute bars")
    tr = _true_range(raw)
    atr = tr.rolling(
        config.atr_lookback_minutes,
        min_periods=max(30, config.atr_lookback_minutes // 2),
    ).median()

    n = config.formation_bars_5m
    index = aligned.index
    values = aligned
    signals: list[RotationSignal] = []
    cooldown_until = -1
    for i in range(n, len(values)):
        excursion_open = index[i]
        excursion_available = excursion_open + pd.Timedelta(minutes=5)
        if excursion_available < start:
            continue
        if excursion_available >= end:
            break
        if i <= cooldown_until:
            continue
        row = values.iloc[i]
        if not bool(row.get("balanced_auction", False)):
            continue
        formation = values.iloc[i - n : i]
        if len(formation) != n or formation["minute_count"].min() != 5:
            continue
        formation_high = float(formation["raw_high_5m"].max())
        formation_low = float(formation["raw_low_5m"].min())
        formation_range = formation_high - formation_low
        formation_center = float(
            np.average(
                formation["raw_close_5m"].to_numpy(dtype=np.float64),
                weights=np.maximum(
                    formation["vol_5m"].to_numpy(dtype=np.float64),
                    1e-12,
                ),
            )
        )
        if not (
            math.isfinite(formation_low)
            and math.isfinite(formation_high)
            and formation_low < formation_center < formation_high
            and formation_range > 0
        ):
            continue
        excursion_atr = float(atr.asof(excursion_available))
        if not math.isfinite(excursion_atr) or excursion_atr <= 0:
            continue
        up = float(row["raw_high_5m"]) >= (
            formation_high + config.minimum_boundary_excursion_atr * excursion_atr
        )
        down = float(row["raw_low_5m"]) <= (
            formation_low - config.minimum_boundary_excursion_atr * excursion_atr
        )
        if up == down:
            continue
        direction = 1 if up else -1
        excursion_flow = direction * (2.0 * float(row["taker_buy_ratio_5m"]) - 1.0)
        if not math.isfinite(excursion_flow) or excursion_flow < config.minimum_outward_flow:
            continue

        boundary = formation_high if direction > 0 else formation_low
        outside_count = 0
        branch: str | None = None
        decision_i: int | None = None
        decision_depth = math.nan
        decision_flow = math.nan
        extreme = float(row["raw_high_5m"] if direction > 0 else row["raw_low_5m"])
        upper = min(len(values), i + config.maximum_classification_bars)
        for j in range(i, upper):
            decision_row = values.iloc[j]
            decision_open = index[j]
            if decision_open != excursion_open + pd.Timedelta(minutes=5 * (j - i)):
                break
            if int(decision_row["minute_count"]) != 5:
                break
            if direction > 0:
                extreme = max(extreme, float(decision_row["raw_high_5m"]))
                outside = float(decision_row["raw_close_5m"]) > boundary
                reclaim_depth = (boundary - float(decision_row["raw_close_5m"])) / excursion_atr
            else:
                extreme = min(extreme, float(decision_row["raw_low_5m"]))
                outside = float(decision_row["raw_close_5m"]) < boundary
                reclaim_depth = (float(decision_row["raw_close_5m"]) - boundary) / excursion_atr
            outside_count = outside_count + 1 if outside else 0
            outward_flow = direction * (2.0 * float(decision_row["taker_buy_ratio_5m"]) - 1.0)
            outward_depth = direction * float(decision_row["depth_imbalance_1pct"])
            inward_flow = -outward_flow
            inward_depth = -outward_depth

            if reclaim_depth >= config.rejection_reclaim_depth_atr:
                if (
                    inward_flow >= config.rejection_inward_flow_min
                    and inward_depth >= config.rejection_inward_depth_min
                ):
                    branch = "REJECTION"
                    decision_i = j
                    decision_depth = inward_depth
                    decision_flow = inward_flow
                    break
            if outside_count >= config.acceptance_consecutive_closes:
                if (
                    outward_flow >= config.acceptance_outward_flow_min
                    and outward_depth >= config.acceptance_outward_depth_min
                ):
                    branch = "ACCEPTANCE"
                    decision_i = j
                    decision_depth = outward_depth
                    decision_flow = outward_flow
                    break

        if branch is None or decision_i is None:
            continue
        decision_open = index[decision_i]
        observed_time = decision_open + pd.Timedelta(minutes=5)
        if not start <= observed_time < end or observed_time not in raw.index:
            continue
        entry_reference = float(raw.at[observed_time, "close"])
        decision_atr = float(atr.asof(observed_time))
        if not math.isfinite(decision_atr) or decision_atr <= 0:
            continue

        if branch == "REJECTION":
            side = "SELL" if direction > 0 else "BUY"
            stop = extreme + direction * config.rejection_stop_buffer_atr * decision_atr
            target = formation_low if direction > 0 else formation_high
            maximum_holding = config.rejection_maximum_holding_minutes
        else:
            side = "BUY" if direction > 0 else "SELL"
            stop = boundary - direction * config.acceptance_stop_inside_atr * decision_atr
            target = boundary + direction * config.acceptance_target_range_extension * formation_range
            maximum_holding = config.acceptance_maximum_holding_minutes

        geometry_valid = (
            stop < entry_reference < target
            if side == "BUY"
            else target < entry_reference < stop
        )
        if not geometry_valid:
            continue
        reward_risk = cost_after_reward_risk(
            entry=entry_reference,
            stop=stop,
            target=target,
            side=side,
            costs=costs,
        )
        if not math.isfinite(reward_risk) or not (
            config.minimum_cost_after_rr
            <= reward_risk
            <= config.maximum_cost_after_rr
        ):
            continue

        observed_ns = int(observed_time.value)
        decision_open_ns = int(decision_open.value)
        score = (formation_range / decision_atr) * (1.0 + abs(decision_flow))
        signals.append(
            RotationSignal(
                scenario_id=f"v55-{branch.lower()}-{observed_ns}",
                observed_time_ns=observed_ns,
                side=side,
                entry_reference=entry_reference,
                stop_price=stop,
                target_price=target,
                cost_after_reward_risk=reward_risk,
                score=score,
                max_hold_minutes=maximum_holding,
                source_feature_open_time_ns=decision_open_ns,
                source_feature_available_time_ns=observed_ns,
                source_max_market_time_ns=observed_ns,
                details={
                    "branch": branch,
                    "excursion_direction": direction,
                    "excursion_open_utc": excursion_open.isoformat(),
                    "decision_open_utc": decision_open.isoformat(),
                    "classification_bars": decision_i - i + 1,
                    "formation_low": formation_low,
                    "formation_center": formation_center,
                    "formation_high": formation_high,
                    "formation_range": formation_range,
                    "formation_range_atr": formation_range / decision_atr,
                    "formation_efficiency": float(row["formation_efficiency"]),
                    "efficiency_threshold": float(row["efficiency_threshold"]),
                    "formation_vpin": float(row["formation_vpin"]),
                    "vpin_threshold": float(row["vpin_threshold"]),
                    "formation_abs_oi_change": float(row["formation_abs_oi_change"]),
                    "oi_abs_threshold": float(row["oi_abs_threshold"]),
                    "excursion_flow": excursion_flow,
                    "decision_flow": decision_flow,
                    "decision_depth": decision_depth,
                    "excursion_extreme": extreme,
                    "atr_1m": decision_atr,
                },
            )
        )
        cooldown_until = decision_i + config.cooldown_bars_after_decision

    signals.sort(key=lambda value: value.observed_time_ns)
    if len({value.observed_time_ns for value in signals}) != len(signals):
        raise ValueError("multiple v55 signals share one observed timestamp")
    for signal in signals:
        if signal.source_max_market_time_ns > signal.observed_time_ns:
            raise AssertionError("future information detected")
        if signal.source_feature_available_time_ns != signal.observed_time_ns:
            raise AssertionError("feature availability mismatch")
        if signal.source_feature_open_time_ns + 5 * NS_MINUTE != signal.observed_time_ns:
            raise AssertionError("decision bar availability mismatch")
    return signals
