"""Quarter-hour algorithmic opening auction for candidate-02 v75.

This module converts completed market observations into deterministic trade
intents. It never simulates fills, positions, fees or NAV; NautilusTrader owns
all execution and account transitions.

The economic mechanism is a synchronized quarter-hour execution burst. The
first ten seconds identify the directional order-flow shock. The frozen prior
15-minute auction supplies the external liquidity boundary. A continuation
trade is released only when the completed opening minute (and, depending on the
mode, a later completed minute) accepts beyond that boundary. A failed-flow
rotation is released only when the same shock is absorbed and price closes back
inside the frozen auction.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from v53_nt_core import CostConfig, RotationSignal, cost_after_reward_risk

NS_MINUTE = 60_000_000_000
UTC = "UTC"
CONTINUATION_MODES = {"IMMEDIATE_ACCEPTANCE", "TWO_CLOSE_ACCEPTANCE", "BOUNDARY_HOLD"}
ALL_MODES = CONTINUATION_MODES | {"FAILED_OPENING_ROTATION"}


@dataclass(frozen=True, slots=True)
class QuarterHourConfig:
    mode: str = "TWO_CLOSE_ACCEPTANCE"
    auction_minutes: int = 15
    prior_days: int = 2
    prior_minimum_events: int = 64
    opening_flow_abs_quantile: float = 0.70
    opening_turnover_quantile: float = 0.60
    opening_abs_return_quantile: float = 0.55
    opening_roundness_quantile: float = 0.55
    minimum_opening_flow_ratio: float = 0.10
    minimum_full_minute_flow_ratio: float = 0.00
    boundary_break_atr: float = 0.05
    boundary_hold_tolerance_atr: float = 0.15
    stop_buffer_atr: float = 0.10
    target_range_extension: float = 1.00
    confirmation_minutes: int = 3
    atr_lookback_minutes: int = 60
    cooldown_minutes: int = 15
    maximum_holding_minutes: int = 240
    minimum_cost_after_rr: float = 0.80
    maximum_cost_after_rr: float = 5.00

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "QuarterHourConfig":
        unknown = sorted(set(values) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"unknown v75 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        if self.mode not in ALL_MODES:
            raise ValueError(f"unknown v75 mode: {self.mode}")
        if self.auction_minutes != 15:
            raise ValueError("v75 auction must remain the quarter-hour auction")
        if self.prior_days < 2 or self.prior_minimum_events < 32:
            raise ValueError("insufficient prospective threshold history")
        for name in (
            "opening_flow_abs_quantile",
            "opening_turnover_quantile",
            "opening_abs_return_quantile",
            "opening_roundness_quantile",
        ):
            value = float(getattr(self, name))
            if not 0.0 < value < 1.0:
                raise ValueError(f"invalid {name}")
        if not 0.0 <= self.minimum_full_minute_flow_ratio < 1.0:
            raise ValueError("invalid full-minute flow floor")
        if not 0.0 < self.minimum_opening_flow_ratio < 1.0:
            raise ValueError("invalid opening flow floor")
        if self.confirmation_minutes not in {1, 2, 3, 4}:
            raise ValueError("invalid confirmation window")
        if self.maximum_holding_minutes <= 0:
            raise ValueError("maximum holding time must be positive")
        if not 0.0 < self.minimum_cost_after_rr <= self.maximum_cost_after_rr:
            raise ValueError("invalid cost-after RR band")


def _quarter_hour_mask(index: pd.DatetimeIndex) -> np.ndarray:
    open_time = index - pd.Timedelta(minutes=1)
    return (open_time.minute % 15 == 0)


def _prior_event_quantile(
    series: pd.Series,
    mask: np.ndarray,
    *,
    days: int,
    quantile: float,
    minimum_events: int,
) -> pd.Series:
    # 96 quarter-hour openings per day; the rolling calendar window retains
    # only event rows and shift(1) prevents the current event entering its own
    # threshold.
    window_rows = days * 24 * 60
    event_series = series.where(mask)
    return event_series.shift(1).rolling(
        window_rows,
        min_periods=minimum_events,
    ).quantile(quantile)


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


def build_state(features: pd.DataFrame, config: QuarterHourConfig) -> pd.DataFrame:
    required = {
        "close",
        "aggressive_signed_quote_1m",
        "aggressive_total_quote_1m",
        "signed_flow_ratio_1m",
        "ask_depth_change_1m",
        "bid_depth_change_1m",
        "depth_imbalance_1pct",
        "qh_opening_10s_signed_quote",
        "qh_opening_10s_total_quote",
        "qh_opening_10s_flow_ratio",
        "qh_opening_10s_return",
        "qh_opening_10s_abs_return",
        "qh_opening_10s_round_share_2",
        "qh_opening_10s_eligible_round_2",
        "qh_rest_50s_flow_ratio",
        "qh_full_minute_return",
    }
    missing = sorted(required - set(features.columns))
    if missing:
        raise ValueError(f"v75 direct feature columns missing: {missing}")

    x = features.copy()
    mask = _quarter_hour_mask(x.index)
    x["is_quarter_hour_open"] = mask.astype(float)
    x["opening_direction"] = np.sign(x["qh_opening_10s_flow_ratio"])
    x["opening_abs_flow"] = x["qh_opening_10s_flow_ratio"].abs()
    x["opening_turnover"] = x["qh_opening_10s_total_quote"]
    x["opening_abs_return"] = x["qh_opening_10s_abs_return"]
    x["opening_price_alignment"] = x["opening_direction"] * x["qh_opening_10s_return"]
    x["minute_price_alignment"] = x["opening_direction"] * x["qh_full_minute_return"]
    x["minute_flow_alignment"] = x["opening_direction"] * x["signed_flow_ratio_1m"]
    x["rest_flow_alignment"] = x["opening_direction"] * x["qh_rest_50s_flow_ratio"]
    x["front_depth_change"] = np.where(
        x["opening_direction"] > 0,
        x["ask_depth_change_1m"],
        x["bid_depth_change_1m"],
    )
    x["back_depth_change"] = np.where(
        x["opening_direction"] > 0,
        x["bid_depth_change_1m"],
        x["ask_depth_change_1m"],
    )
    x["opening_flow_threshold"] = _prior_event_quantile(
        x["opening_abs_flow"],
        mask,
        days=config.prior_days,
        quantile=config.opening_flow_abs_quantile,
        minimum_events=config.prior_minimum_events,
    )
    x["opening_turnover_threshold"] = _prior_event_quantile(
        x["opening_turnover"],
        mask,
        days=config.prior_days,
        quantile=config.opening_turnover_quantile,
        minimum_events=config.prior_minimum_events,
    )
    x["opening_return_threshold"] = _prior_event_quantile(
        x["opening_abs_return"],
        mask,
        days=config.prior_days,
        quantile=config.opening_abs_return_quantile,
        minimum_events=config.prior_minimum_events,
    )
    x["opening_roundness_threshold"] = _prior_event_quantile(
        x["qh_opening_10s_round_share_2"],
        mask,
        days=config.prior_days,
        quantile=config.opening_roundness_quantile,
        minimum_events=max(32, config.prior_minimum_events // 2),
    )
    # Roundness is a diagnostic signature, not a causal requirement. The
    # boolean is recorded and enters score, but an opening is not discarded
    # when too few eligible trades make the statistic unavailable.
    x["algorithmic_roundness_signature"] = (
        (x["qh_opening_10s_eligible_round_2"] >= 5.0)
        & (x["qh_opening_10s_round_share_2"] <= x["opening_roundness_threshold"])
    )
    return x


def _finite(row: pd.Series, names: tuple[str, ...]) -> bool:
    return all(math.isfinite(float(row[name])) for name in names)


def _append_signal(
    output: list[RotationSignal],
    *,
    config: QuarterHourConfig,
    costs: CostConfig,
    observed: pd.Timestamp,
    side: str,
    entry: float,
    stop: float,
    target: float,
    score: float,
    details: Mapping[str, Any],
) -> bool:
    geometry = stop < entry < target if side == "BUY" else target < entry < stop
    if not geometry:
        return False
    rr = cost_after_reward_risk(
        entry=entry,
        stop=stop,
        target=target,
        side=side,
        costs=costs,
    )
    if not math.isfinite(rr) or not config.minimum_cost_after_rr <= rr <= config.maximum_cost_after_rr:
        return False
    ns = int(observed.value)
    output.append(
        RotationSignal(
            scenario_id=f"v75-{config.mode.lower()}-{ns}",
            observed_time_ns=ns,
            side=side,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            cost_after_reward_risk=rr,
            score=score,
            max_hold_minutes=config.maximum_holding_minutes,
            source_feature_open_time_ns=ns - NS_MINUTE,
            source_feature_available_time_ns=ns,
            source_max_market_time_ns=ns,
            details=dict(details),
        )
    )
    return True


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: QuarterHourConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = pd.Timestamp(evaluation_start)
    end = pd.Timestamp(evaluation_end)
    start = start.tz_localize(UTC) if start.tzinfo is None else start.tz_convert(UTC)
    end = end.tz_localize(UTC) if end.tzinfo is None else end.tz_convert(UTC)
    if end <= start:
        raise ValueError("evaluation end must be after start")

    x = state.join(
        raw[["open", "high", "low", "close"]].rename(
            columns={
                "open": "raw_open",
                "high": "raw_high",
                "low": "raw_low",
                "close": "raw_close",
            }
        ),
        how="inner",
    )
    atr = _true_range(raw).rolling(
        config.atr_lookback_minutes,
        min_periods=max(30, config.atr_lookback_minutes // 2),
    ).median()
    signals: list[RotationSignal] = []
    cooldown_until = -1

    base_fields = (
        "opening_abs_flow",
        "opening_flow_threshold",
        "opening_turnover",
        "opening_turnover_threshold",
        "opening_abs_return",
        "opening_return_threshold",
        "opening_price_alignment",
        "minute_price_alignment",
        "minute_flow_alignment",
        "rest_flow_alignment",
    )

    candidates = x.loc[(x.index >= start) & (x.index < end) & (x["is_quarter_hour_open"] > 0.5)]
    for ts, row in candidates.iterrows():
        ts_ns = int(ts.value)
        if ts_ns <= cooldown_until or not _finite(row, base_fields):
            continue
        direction = int(np.sign(float(row["opening_direction"])))
        if direction == 0:
            continue
        if float(row["opening_abs_flow"]) < max(
            config.minimum_opening_flow_ratio,
            float(row["opening_flow_threshold"]),
        ):
            continue
        if float(row["opening_turnover"]) < float(row["opening_turnover_threshold"]):
            continue
        if float(row["opening_abs_return"]) < float(row["opening_return_threshold"]):
            continue
        if float(row["opening_price_alignment"]) <= 0.0:
            continue

        formation = raw.loc[
            (raw.index >= ts - pd.Timedelta(minutes=config.auction_minutes))
            & (raw.index <= ts - pd.Timedelta(minutes=1))
        ]
        if len(formation) < config.auction_minutes - 1:
            continue
        high = float(formation["high"].max())
        low = float(formation["low"].min())
        width = high - low
        atr_value = float(atr.asof(ts))
        if not all(math.isfinite(v) for v in (high, low, width, atr_value)) or width <= 0.0 or atr_value <= 0.0:
            continue
        boundary = high if direction > 0 else low
        open_high = float(row["raw_high"])
        open_low = float(row["raw_low"])
        open_close = float(row["raw_close"])
        open_origin = float(row["raw_open"])
        break_ok = (
            open_high >= high + config.boundary_break_atr * atr_value
            if direction > 0
            else open_low <= low - config.boundary_break_atr * atr_value
        )
        close_outside = open_close > high if direction > 0 else open_close < low
        score = (
            float(row["opening_abs_flow"])
            * max(float(row["opening_turnover"]) / max(float(row["opening_turnover_threshold"]), 1e-12), 1.0)
            * (1.10 if bool(row["algorithmic_roundness_signature"]) else 1.0)
        )

        if config.mode in CONTINUATION_MODES:
            if not break_ok or not close_outside:
                continue
            if float(row["minute_price_alignment"]) <= 0.0:
                continue
            if float(row["minute_flow_alignment"]) < config.minimum_full_minute_flow_ratio:
                continue
            observed = pd.Timestamp(ts)
            entry = open_close
            confirmation_details: dict[str, Any] = {"confirmation": "opening_minute_close"}

            if config.mode == "TWO_CLOSE_ACCEPTANCE":
                future = x.loc[(x.index > ts) & (x.index <= ts + pd.Timedelta(minutes=config.confirmation_minutes))]
                found = None
                for obs, r in future.iterrows():
                    held = float(r["raw_close"]) > boundary if direction > 0 else float(r["raw_close"]) < boundary
                    flow_ok = direction * float(r["signed_flow_ratio_1m"]) >= -0.05
                    if held and flow_ok:
                        found = (pd.Timestamp(obs), float(r["raw_close"]))
                        break
                    # A close back inside invalidates acceptance rather than
                    # being silently skipped until a later re-break.
                    if not held:
                        break
                if found is None:
                    continue
                observed, entry = found
                confirmation_details = {"confirmation": "second_completed_close_outside"}
            elif config.mode == "BOUNDARY_HOLD":
                future = x.loc[(x.index > ts) & (x.index <= ts + pd.Timedelta(minutes=config.confirmation_minutes))]
                found = None
                for obs, r in future.iterrows():
                    close_value = float(r["raw_close"])
                    held = close_value > boundary if direction > 0 else close_value < boundary
                    touched = (
                        float(r["raw_low"]) <= boundary + config.boundary_hold_tolerance_atr * atr_value
                        if direction > 0
                        else float(r["raw_high"]) >= boundary - config.boundary_hold_tolerance_atr * atr_value
                    )
                    flow_ok = direction * float(r["signed_flow_ratio_1m"]) >= -0.05
                    if held and touched and flow_ok:
                        found = (pd.Timestamp(obs), close_value)
                        break
                    if not held:
                        break
                if found is None:
                    continue
                observed, entry = found
                confirmation_details = {"confirmation": "completed_boundary_retest_hold"}

            if direction > 0:
                side = "BUY"
                stop = min(open_origin, open_low, boundary) - config.stop_buffer_atr * atr_value
                target = high + config.target_range_extension * width
            else:
                side = "SELL"
                stop = max(open_origin, open_high, boundary) + config.stop_buffer_atr * atr_value
                target = low - config.target_range_extension * width
            details = {
                "mode": config.mode,
                "quarter_hour_open_utc": (ts - pd.Timedelta(minutes=1)).isoformat(),
                "frozen_boundary_high": high,
                "frozen_boundary_low": low,
                "opening_origin": open_origin,
                "opening_high": open_high,
                "opening_low": open_low,
                "opening_close": open_close,
                "opening_10s_flow_ratio": float(row["qh_opening_10s_flow_ratio"]),
                "opening_10s_turnover": float(row["qh_opening_10s_total_quote"]),
                "opening_10s_return": float(row["qh_opening_10s_return"]),
                "opening_10s_round_share_2": float(row["qh_opening_10s_round_share_2"]),
                "algorithmic_roundness_signature": bool(row["algorithmic_roundness_signature"]),
                "full_minute_flow_alignment": float(row["minute_flow_alignment"]),
                "rest_50s_flow_alignment": float(row["rest_flow_alignment"]),
                "front_depth_change": float(row["front_depth_change"]),
                "entry_order_type": "MARKET",
                **confirmation_details,
            }
            if _append_signal(
                signals,
                config=config,
                costs=costs,
                observed=observed,
                side=side,
                entry=entry,
                stop=stop,
                target=target,
                score=score,
                details=details,
            ):
                cooldown_until = int(observed.value) + config.cooldown_minutes * NS_MINUTE
            continue

        # Mutually exclusive failed-opening branch. The first 10-second shock
        # must be strong, but the completed minute must close back inside the
        # frozen auction and the remaining 50 seconds must oppose the opening
        # direction. This is not the generic absorption state rejected in v66;
        # the synchronized quarter-hour origin is a required causal condition.
        if config.mode == "FAILED_OPENING_ROTATION":
            swept = (
                open_high >= high + config.boundary_break_atr * atr_value
                if direction > 0
                else open_low <= low - config.boundary_break_atr * atr_value
            )
            reclaimed = open_close < high if direction > 0 else open_close > low
            rest_reversed = float(row["rest_flow_alignment"]) < 0.0
            price_failed = float(row["minute_price_alignment"]) <= 0.0 or reclaimed
            if not (swept and reclaimed and rest_reversed and price_failed):
                continue
            if direction > 0:
                side = "SELL"
                stop = open_high + config.stop_buffer_atr * atr_value
                target = low
            else:
                side = "BUY"
                stop = open_low - config.stop_buffer_atr * atr_value
                target = high
            details = {
                "mode": config.mode,
                "quarter_hour_open_utc": (ts - pd.Timedelta(minutes=1)).isoformat(),
                "frozen_boundary_high": high,
                "frozen_boundary_low": low,
                "opening_origin": open_origin,
                "opening_high": open_high,
                "opening_low": open_low,
                "opening_close": open_close,
                "opening_10s_flow_ratio": float(row["qh_opening_10s_flow_ratio"]),
                "opening_10s_turnover": float(row["qh_opening_10s_total_quote"]),
                "opening_10s_return": float(row["qh_opening_10s_return"]),
                "opening_10s_round_share_2": float(row["qh_opening_10s_round_share_2"]),
                "algorithmic_roundness_signature": bool(row["algorithmic_roundness_signature"]),
                "full_minute_flow_alignment": float(row["minute_flow_alignment"]),
                "rest_50s_flow_alignment": float(row["rest_flow_alignment"]),
                "front_depth_change": float(row["front_depth_change"]),
                "entry_order_type": "MARKET",
                "confirmation": "completed_opening_minute_failed_and_reclaimed",
            }
            if _append_signal(
                signals,
                config=config,
                costs=costs,
                observed=pd.Timestamp(ts),
                side=side,
                entry=open_close,
                stop=stop,
                target=target,
                score=score,
                details=details,
            ):
                cooldown_until = ts_ns + config.cooldown_minutes * NS_MINUTE

    # A single timestamp cannot release competing directions. When modes are
    # run separately this is normally moot, but the guard remains explicit.
    result: list[RotationSignal] = []
    seen: set[int] = set()
    for signal in sorted(signals, key=lambda value: value.observed_time_ns):
        if signal.observed_time_ns in seen:
            continue
        seen.add(signal.observed_time_ns)
        if signal.source_max_market_time_ns > signal.observed_time_ns:
            raise AssertionError("future information detected")
        result.append(signal)
    return result


__all__ = [
    "QuarterHourConfig",
    "build_state",
    "build_rotation_signals",
]
