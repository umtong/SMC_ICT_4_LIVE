"""Quarter-hour metaorder wave for candidate-02 v76.

This module only turns completed observations into deterministic trade intents.
NautilusTrader owns all orders, fills, positions, fees and account NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from v53_nt_core import CostConfig, RotationSignal, cost_after_reward_risk
from v75_quarter_hour_core import QuarterHourConfig, build_state as build_quarter_hour_state

NS_MINUTE = 60_000_000_000
UTC = "UTC"


@dataclass(frozen=True, slots=True)
class MetaorderWaveConfig(QuarterHourConfig):
    accepted_range_minutes: int = 30
    target_range_multiple: float = 1.25
    stop_range_multiple: float = 1.00
    minimum_rest_flow_alignment: float = 0.0
    minimum_minute_flow_alignment: float = 0.10
    maximum_front_depth_change: float = 0.0

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "MetaorderWaveConfig":
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"unknown v76 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        if self.mode != "METAORDER_WAVE":
            raise ValueError("v76 mode must be METAORDER_WAVE")
        if self.auction_minutes != 15:
            raise ValueError("v76 origin must remain the quarter-hour auction")
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
        if self.accepted_range_minutes not in {15, 30, 60}:
            raise ValueError("accepted_range_minutes must be 15, 30 or 60")
        if not 0.5 <= self.target_range_multiple <= 2.0:
            raise ValueError("target_range_multiple outside structural range")
        if not 0.5 <= self.stop_range_multiple <= 1.5:
            raise ValueError("stop_range_multiple outside structural range")
        if not -1.0 < self.minimum_rest_flow_alignment < 1.0:
            raise ValueError("invalid rest flow threshold")
        if not -1.0 < self.minimum_minute_flow_alignment < 1.0:
            raise ValueError("invalid minute flow threshold")


def build_state(features: pd.DataFrame, config: MetaorderWaveConfig) -> pd.DataFrame:
    return build_quarter_hour_state(features, config)


def _finite(row: pd.Series, names: tuple[str, ...]) -> bool:
    return all(math.isfinite(float(row[name])) for name in names)


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: MetaorderWaveConfig,
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
            columns={"open": "raw_open", "high": "raw_high", "low": "raw_low", "close": "raw_close"}
        ),
        how="inner",
    )
    candidates = x.loc[(x.index >= start) & (x.index < end) & (x["is_quarter_hour_open"] > 0.5)]
    signals: list[RotationSignal] = []
    cooldown_until = -1
    fields = (
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
        "front_depth_change",
    )

    for ts, row in candidates.iterrows():
        ts_ns = int(ts.value)
        if ts_ns <= cooldown_until or not _finite(row, fields):
            continue
        direction = int(np.sign(float(row["opening_direction"])))
        if direction == 0:
            continue
        if float(row["opening_abs_flow"]) < max(config.minimum_opening_flow_ratio, float(row["opening_flow_threshold"])):
            continue
        if float(row["opening_turnover"]) < float(row["opening_turnover_threshold"]):
            continue
        if float(row["opening_abs_return"]) < float(row["opening_return_threshold"]):
            continue
        if float(row["opening_price_alignment"]) <= 0.0 or float(row["minute_price_alignment"]) <= 0.0:
            continue
        if float(row["minute_flow_alignment"]) < config.minimum_minute_flow_alignment:
            continue
        if float(row["rest_flow_alignment"]) <= config.minimum_rest_flow_alignment:
            continue
        # In the shock direction, negative front-side depth change means liquidity was withdrawn.
        if float(row["front_depth_change"]) >= config.maximum_front_depth_change:
            continue

        formation = raw.loc[
            (raw.index >= ts - pd.Timedelta(minutes=config.accepted_range_minutes))
            & (raw.index <= ts - pd.Timedelta(minutes=1))
        ]
        if len(formation) < config.accepted_range_minutes - 1:
            continue
        # Completed close range represents the accepted auction, rather than wick-only excursions.
        accepted_high = float(formation["close"].max())
        accepted_low = float(formation["close"].min())
        width = accepted_high - accepted_low
        entry = float(row["raw_close"])
        if not all(math.isfinite(value) for value in (accepted_high, accepted_low, width, entry)) or width <= 0.0:
            continue

        side = "BUY" if direction > 0 else "SELL"
        stop = entry - direction * config.stop_range_multiple * width
        target = entry + direction * config.target_range_multiple * width
        geometry = stop < entry < target if side == "BUY" else target < entry < stop
        if not geometry:
            continue
        rr = cost_after_reward_risk(entry=entry, stop=stop, target=target, side=side, costs=costs)
        if not math.isfinite(rr) or not config.minimum_cost_after_rr <= rr <= config.maximum_cost_after_rr:
            continue

        turnover_ratio = float(row["opening_turnover"]) / max(float(row["opening_turnover_threshold"]), 1e-12)
        withdrawal = abs(min(float(row["front_depth_change"]), 0.0))
        score = float(row["opening_abs_flow"]) * max(turnover_ratio, 1.0) * (1.0 + withdrawal)
        details = {
            "mode": config.mode,
            "quarter_hour_open_utc": (ts - pd.Timedelta(minutes=1)).isoformat(),
            "accepted_range_minutes": config.accepted_range_minutes,
            "accepted_close_high": accepted_high,
            "accepted_close_low": accepted_low,
            "accepted_close_width": width,
            "target_range_multiple": config.target_range_multiple,
            "stop_range_multiple": config.stop_range_multiple,
            "opening_10s_flow_ratio": float(row["qh_opening_10s_flow_ratio"]),
            "opening_10s_turnover": float(row["qh_opening_10s_total_quote"]),
            "opening_10s_return": float(row["qh_opening_10s_return"]),
            "opening_10s_round_share_2": float(row["qh_opening_10s_round_share_2"]),
            "algorithmic_roundness_signature": bool(row["algorithmic_roundness_signature"]),
            "full_minute_flow_alignment": float(row["minute_flow_alignment"]),
            "rest_50s_flow_alignment": float(row["rest_flow_alignment"]),
            "front_depth_change": float(row["front_depth_change"]),
            "entry_order_type": "MARKET",
            "confirmation": "completed_quarter_hour_opening_minute_with_persistent_flow_and_front_depth_withdrawal",
        }
        signals.append(
            RotationSignal(
                scenario_id=f"v76-{config.accepted_range_minutes}m-{ts_ns}",
                observed_time_ns=ts_ns,
                side=side,
                entry_reference=entry,
                stop_price=stop,
                target_price=target,
                cost_after_reward_risk=rr,
                score=score,
                max_hold_minutes=config.maximum_holding_minutes,
                source_feature_open_time_ns=ts_ns - NS_MINUTE,
                source_feature_available_time_ns=ts_ns,
                source_max_market_time_ns=ts_ns,
                details=details,
            )
        )
        cooldown_until = ts_ns + config.cooldown_minutes * NS_MINUTE

    for signal in signals:
        if signal.source_max_market_time_ns > signal.observed_time_ns:
            raise AssertionError("future information detected")
    return signals


__all__ = ["MetaorderWaveConfig", "build_state", "build_rotation_signals"]
