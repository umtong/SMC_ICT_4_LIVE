"""Quarter-hour impact-retention state machine for candidate-02 v102.

A quarter-hour opening burst is only an event clock. Direction is assigned after
three separate causal tests:

1. the first ten seconds and the completed opening minute carry aligned
   aggressive flow and price impact;
2. front-side depth does not refill materially during that minute;
3. after a fixed number of completed minutes the opening impact is still
   retained and aggregate aggressive flow has not changed sign.

Only the retained-impact state is traded. A return through the pre-burst close
invalidates the price-discovery interpretation. The objective is one frozen
60-minute accepted-close range in the delivery direction.

This module emits deterministic trade intents only. NautilusTrader owns every
order, fill, fee, position and account-NAV transition.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from v53_nt_core import CostConfig, RotationSignal, cost_after_reward_risk
from v75_quarter_hour_core import QuarterHourConfig, build_state as build_quarter_hour_state

UTC = "UTC"
NS_MINUTE = 60_000_000_000


@dataclass(frozen=True, slots=True)
class ImpactRetentionConfig(QuarterHourConfig):
    accepted_range_minutes: int = 60
    response_minutes: int = 3
    minimum_impact_retention: float = 1.0
    minimum_response_flow_alignment: float = 0.0
    maximum_front_depth_change: float = 0.05
    stop_beyond_preburst_atr: float = 0.10
    target_range_multiple: float = 1.00

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "ImpactRetentionConfig":
        unknown = sorted(set(values) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"unknown v102 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        # Keep a valid base mode because the inherited state builder uses the
        # common quarter-hour contract; v102 supplies its own scenario logic.
        if self.mode != "TWO_CLOSE_ACCEPTANCE":
            raise ValueError("v102 base mode must remain TWO_CLOSE_ACCEPTANCE")
        if self.auction_minutes != 15:
            raise ValueError("v102 event clock must remain quarter-hour")
        if self.prior_days < 2 or self.prior_minimum_events < 32:
            raise ValueError("insufficient causal threshold history")
        for name in (
            "opening_flow_abs_quantile",
            "opening_turnover_quantile",
            "opening_abs_return_quantile",
            "opening_roundness_quantile",
        ):
            if not 0.0 < float(getattr(self, name)) < 1.0:
                raise ValueError(f"invalid {name}")
        if self.accepted_range_minutes not in {30, 60, 90}:
            raise ValueError("accepted range must be 30, 60 or 90 minutes")
        if self.response_minutes not in {2, 3, 4}:
            raise ValueError("response window must be 2, 3 or 4 completed minutes")
        if not 0.5 <= self.minimum_impact_retention <= 2.0:
            raise ValueError("invalid impact-retention threshold")
        if not -1.0 < self.minimum_response_flow_alignment < 1.0:
            raise ValueError("invalid response-flow threshold")
        if not -1.0 < self.maximum_front_depth_change < 2.0:
            raise ValueError("invalid front-depth threshold")
        if not 0.0 <= self.stop_beyond_preburst_atr <= 1.0:
            raise ValueError("invalid pre-burst invalidation buffer")
        if not 0.25 <= self.target_range_multiple <= 2.0:
            raise ValueError("invalid accepted-range objective")
        if self.maximum_holding_minutes <= 0:
            raise ValueError("maximum holding time must be positive")
        if not 0.0 < self.minimum_cost_after_rr <= self.maximum_cost_after_rr:
            raise ValueError("invalid cost-after reward/risk band")


def build_state(features: pd.DataFrame, config: ImpactRetentionConfig) -> pd.DataFrame:
    return build_quarter_hour_state(features, config)


def _finite(row: pd.Series, names: tuple[str, ...]) -> bool:
    return all(math.isfinite(float(row[name])) for name in names)


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


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: ImpactRetentionConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = pd.Timestamp(evaluation_start)
    end = pd.Timestamp(evaluation_end)
    start = start.tz_localize(UTC) if start.tzinfo is None else start.tz_convert(UTC)
    end = end.tz_localize(UTC) if end.tzinfo is None else end.tz_convert(UTC)
    if end <= start:
        raise ValueError("evaluation end must be after start")

    raw_view = raw[["open", "high", "low", "close"]].copy().sort_index()
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
    atr = _true_range(raw_view).rolling(
        config.atr_lookback_minutes,
        min_periods=max(30, config.atr_lookback_minutes // 2),
    ).median().shift(1)

    candidates = x.loc[
        (x.index >= start)
        & (x.index < end)
        & (x["is_quarter_hour_open"] > 0.5)
    ]
    signals: list[RotationSignal] = []
    cooldown_until_ns = -1
    event_fields = (
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
        "raw_close",
    )

    for ts, row in candidates.iterrows():
        ts_ns = int(ts.value)
        if ts_ns <= cooldown_until_ns or not _finite(row, event_fields):
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
        if float(row["minute_price_alignment"]) <= 0.0:
            continue
        if float(row["minute_flow_alignment"]) < config.minimum_full_minute_flow_ratio:
            continue
        if float(row["rest_flow_alignment"]) <= 0.0:
            continue
        # A small refill is tolerated; a material refill means the opening
        # shock did not leave a persistent liquidity vacuum.
        if float(row["front_depth_change"]) > config.maximum_front_depth_change:
            continue

        previous_ts = ts - pd.Timedelta(minutes=1)
        if previous_ts not in x.index:
            continue
        preburst_close = float(x.loc[previous_ts, "raw_close"])
        opening_close = float(row["raw_close"])
        opening_impulse = direction * (opening_close - preburst_close)
        if not math.isfinite(opening_impulse) or opening_impulse <= 0.0:
            continue

        observed = ts + pd.Timedelta(minutes=config.response_minutes)
        if observed >= end or observed not in x.index:
            continue
        response = x.loc[(x.index > ts) & (x.index <= observed)]
        if len(response) != config.response_minutes:
            continue
        response_close = float(x.loc[observed, "raw_close"])
        retained_impact = direction * (response_close - preburst_close) / opening_impulse
        response_flow_alignment = direction * float(
            response["signed_flow_ratio_1m"].mean()
        )
        if retained_impact < config.minimum_impact_retention:
            continue
        if response_flow_alignment < config.minimum_response_flow_alignment:
            continue

        formation = raw_view.loc[
            (raw_view.index >= ts - pd.Timedelta(minutes=config.accepted_range_minutes))
            & (raw_view.index <= previous_ts)
        ]
        if len(formation) < config.accepted_range_minutes - 1:
            continue
        accepted_high = float(formation["close"].max())
        accepted_low = float(formation["close"].min())
        accepted_width = accepted_high - accepted_low
        atr_value = float(atr.asof(observed))
        if not all(
            math.isfinite(v)
            for v in (accepted_high, accepted_low, accepted_width, atr_value)
        ):
            continue
        if accepted_width <= 0.0 or atr_value <= 0.0:
            continue

        entry = response_close
        side = "BUY" if direction > 0 else "SELL"
        stop = preburst_close - direction * config.stop_beyond_preburst_atr * atr_value
        target = entry + direction * config.target_range_multiple * accepted_width
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
        if not math.isfinite(rr):
            continue
        if not config.minimum_cost_after_rr <= rr <= config.maximum_cost_after_rr:
            continue

        turnover_ratio = float(row["opening_turnover"]) / max(
            float(row["opening_turnover_threshold"]),
            1e-12,
        )
        score = (
            retained_impact
            * max(response_flow_alignment + 1.0, 0.0)
            * max(turnover_ratio, 1.0)
            * (1.0 + abs(min(float(row["front_depth_change"]), 0.0)))
        )
        details = {
            "state": "QUARTER_HOUR_RETAINED_IMPACT_PRICE_DISCOVERY",
            "quarter_hour_opening_minute_close_utc": ts.isoformat(),
            "response_close_utc": observed.isoformat(),
            "response_minutes": config.response_minutes,
            "opening_direction": "UP" if direction > 0 else "DOWN",
            "preburst_close": preburst_close,
            "opening_close": opening_close,
            "opening_impulse": opening_impulse,
            "retained_impact_ratio": retained_impact,
            "response_flow_alignment": response_flow_alignment,
            "opening_10s_flow_ratio": float(row["qh_opening_10s_flow_ratio"]),
            "opening_turnover": float(row["opening_turnover"]),
            "opening_turnover_ratio": turnover_ratio,
            "front_depth_change": float(row["front_depth_change"]),
            "accepted_range_minutes": config.accepted_range_minutes,
            "accepted_close_high": accepted_high,
            "accepted_close_low": accepted_low,
            "accepted_close_width": accepted_width,
            "invalidation": "full opening impact retrace through the pre-burst close plus prior ATR buffer",
            "objective": "one frozen accepted-close range in the retained-impact delivery direction",
            "entry_order_type": "MARKET",
        }
        observed_ns = int(observed.value)
        signals.append(
            RotationSignal(
                scenario_id=f"v102-retained-impact-{config.response_minutes}m-{observed_ns}",
                observed_time_ns=observed_ns,
                side=side,
                entry_reference=entry,
                stop_price=stop,
                target_price=target,
                cost_after_reward_risk=rr,
                score=float(score),
                max_hold_minutes=config.maximum_holding_minutes,
                source_feature_open_time_ns=ts_ns - NS_MINUTE,
                source_feature_available_time_ns=observed_ns,
                source_max_market_time_ns=observed_ns,
                details=details,
            )
        )
        cooldown_until_ns = observed_ns + config.cooldown_minutes * NS_MINUTE

    signals.sort(key=lambda signal: (signal.observed_time_ns, -signal.score, signal.scenario_id))
    unique: list[RotationSignal] = []
    seen: set[int] = set()
    for signal in signals:
        if signal.observed_time_ns in seen:
            continue
        if signal.source_max_market_time_ns > signal.observed_time_ns:
            raise AssertionError("future information detected in v102")
        seen.add(signal.observed_time_ns)
        unique.append(signal)
    return unique


__all__ = ["ImpactRetentionConfig", "build_state", "build_rotation_signals"]
