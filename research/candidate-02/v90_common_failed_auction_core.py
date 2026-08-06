"""Common spot-perpetual failed-auction state machine for candidate-02 v90.

A common quarter-hour shock is only an event clock.  The strategy trades the
opposite direction only after that shock removes a frozen external auction
boundary, fails to extend, and closes back inside the accepted range.  It
separates passive absorption with persistent aggressive flow from an active
flow reversal.  NautilusTrader owns all orders, fills, fees, positions and NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from v53_nt_core import CostConfig, RotationSignal, cost_after_reward_risk
from v89_cross_market_impact_core import build_state as build_cross_market_state

NS_MINUTE = 60_000_000_000
UTC = "UTC"
MODES = {"STATE_PORTFOLIO", "PERSISTENT_FLOW_ABSORPTION", "ACTIVE_FLOW_REVERSAL"}


@dataclass(frozen=True, slots=True)
class CommonFailedAuctionConfig:
    mode: str = "STATE_PORTFOLIO"
    prior_days: int = 2
    prior_minimum_events: int = 64
    opening_flow_abs_quantile: float = 0.55
    opening_turnover_quantile: float = 0.45
    opening_abs_return_quantile: float = 0.50
    minimum_opening_flow_ratio: float = 0.06
    minimum_common_spot_participation: float = 0.20
    minimum_common_spot_flow_alignment: float = -0.05
    maximum_common_basis_share: float = 0.80
    minimum_event_rest_flow_alignment: float = -0.20
    accepted_range_minutes: int = 30
    boundary_break_atr: float = 0.05
    confirmation_minutes: int = 3
    reclaim_depth_atr: float = 0.02
    maximum_additional_extension_ratio: float = 0.50
    minimum_failure_retracement_ratio: float = 0.35
    minimum_persistent_confirmation_flow: float = 0.05
    maximum_reversal_confirmation_flow: float = -0.05
    minimum_basis_decay_share: float = -0.10
    stop_buffer_atr: float = 0.15
    atr_lookback_minutes: int = 60
    cooldown_minutes: int = 10
    maximum_holding_minutes: int = 180
    minimum_cost_after_rr: float = 0.80
    maximum_cost_after_rr: float = 5.00

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "CommonFailedAuctionConfig":
        unknown = sorted(set(values) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"unknown v90 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown v90 mode: {self.mode}")
        if self.prior_days < 2 or self.prior_minimum_events < 32:
            raise ValueError("insufficient prospective event history")
        for name in (
            "opening_flow_abs_quantile",
            "opening_turnover_quantile",
            "opening_abs_return_quantile",
        ):
            value = float(getattr(self, name))
            if not 0.0 < value < 1.0:
                raise ValueError(f"invalid {name}")
        if self.accepted_range_minutes not in {15, 30, 60}:
            raise ValueError("accepted_range_minutes must be 15, 30 or 60")
        if self.confirmation_minutes not in {1, 2, 3, 4, 5}:
            raise ValueError("confirmation_minutes outside structural range")
        if not 0.0 < self.minimum_opening_flow_ratio < 1.0:
            raise ValueError("invalid opening flow floor")
        if self.minimum_common_spot_participation < 0.0:
            raise ValueError("invalid spot participation floor")
        if self.maximum_reversal_confirmation_flow >= self.minimum_persistent_confirmation_flow:
            raise ValueError("confirmation-flow states must remain separated")
        if self.boundary_break_atr < 0.0 or self.reclaim_depth_atr < 0.0:
            raise ValueError("invalid boundary geometry")
        if not 0.0 <= self.maximum_additional_extension_ratio <= 2.0:
            raise ValueError("invalid extension ratio")
        if not 0.0 <= self.minimum_failure_retracement_ratio <= 3.0:
            raise ValueError("invalid retracement ratio")
        if self.atr_lookback_minutes < 30 or self.maximum_holding_minutes <= 0:
            raise ValueError("invalid horizon")
        if self.stop_buffer_atr < 0.0:
            raise ValueError("invalid stop buffer")
        if not 0.0 < self.minimum_cost_after_rr <= self.maximum_cost_after_rr:
            raise ValueError("invalid cost-after reward/risk band")


def build_state(features: pd.DataFrame, config: CommonFailedAuctionConfig) -> pd.DataFrame:
    # The v89 state builder performs only causal event-feature construction and
    # accepts any config exposing the common adaptive-threshold fields.
    return build_cross_market_state(features, config)  # type: ignore[arg-type]


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


def _finite(row: pd.Series, names: tuple[str, ...]) -> bool:
    return all(math.isfinite(float(row[name])) for name in names)


def _append_signal(
    output: list[RotationSignal],
    *,
    config: CommonFailedAuctionConfig,
    costs: CostConfig,
    observed: pd.Timestamp,
    side: str,
    entry: float,
    stop: float,
    target: float,
    score: float,
    source_open_time_ns: int,
    details: Mapping[str, Any],
) -> bool:
    geometry = stop < entry < target if side == "BUY" else target < entry < stop
    if not geometry:
        return False
    rr = cost_after_reward_risk(entry=entry, stop=stop, target=target, side=side, costs=costs)
    if not math.isfinite(rr) or not config.minimum_cost_after_rr <= rr <= config.maximum_cost_after_rr:
        return False
    observed_ns = int(observed.value)
    output.append(
        RotationSignal(
            scenario_id=f"v90-{str(details['state']).lower()}-{observed_ns}",
            observed_time_ns=observed_ns,
            side=side,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            cost_after_reward_risk=rr,
            score=score,
            max_hold_minutes=config.maximum_holding_minutes,
            source_feature_open_time_ns=source_open_time_ns,
            source_feature_available_time_ns=observed_ns,
            source_max_market_time_ns=observed_ns,
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
    config: CommonFailedAuctionConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = pd.Timestamp(evaluation_start)
    end = pd.Timestamp(evaluation_end)
    start = start.tz_localize(UTC) if start.tzinfo is None else start.tz_convert(UTC)
    end = end.tz_localize(UTC) if end.tzinfo is None else end.tz_convert(UTC)
    if end <= start:
        raise ValueError("evaluation end must be after start")

    joined = state.join(
        raw[["open", "high", "low", "close"]].rename(
            columns={"open": "raw_open", "high": "raw_high", "low": "raw_low", "close": "raw_close"}
        ),
        how="inner",
    )
    atr = _true_range(raw).rolling(
        config.atr_lookback_minutes,
        min_periods=max(30, config.atr_lookback_minutes // 2),
    ).median()
    candidates = joined.loc[
        (joined.index >= start)
        & (joined.index < end)
        & (joined["is_quarter_hour_open"] > 0.5)
    ]
    signals: list[RotationSignal] = []
    cooldown_until = -1
    fields = (
        "opening_abs_flow",
        "opening_flow_threshold",
        "opening_turnover",
        "opening_turnover_threshold",
        "opening_abs_return",
        "opening_return_threshold",
        "perp_open_alignment",
        "spot_open_alignment",
        "spot_flow_alignment",
        "spot_participation",
        "basis_share",
        "perp_rest_flow_alignment",
        "front_depth_change",
        "perp_spot_log_basis",
        "raw_open",
        "raw_high",
        "raw_low",
        "raw_close",
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
        opening_impact = float(row["perp_open_alignment"])
        if opening_impact <= 0.0:
            continue
        if float(row["spot_open_alignment"]) <= 0.0:
            continue
        if float(row["spot_participation"]) < config.minimum_common_spot_participation:
            continue
        if float(row["spot_flow_alignment"]) < config.minimum_common_spot_flow_alignment:
            continue
        if float(row["basis_share"]) > config.maximum_common_basis_share:
            continue
        if float(row["perp_rest_flow_alignment"]) < config.minimum_event_rest_flow_alignment:
            continue

        formation = raw.loc[
            (raw.index >= ts - pd.Timedelta(minutes=config.accepted_range_minutes))
            & (raw.index <= ts - pd.Timedelta(minutes=1))
        ]
        if len(formation) < config.accepted_range_minutes - 1:
            continue
        accepted_high = float(formation["close"].max())
        accepted_low = float(formation["close"].min())
        accepted_width = accepted_high - accepted_low
        atr_value = float(atr.asof(ts))
        if not all(math.isfinite(v) for v in (accepted_high, accepted_low, accepted_width, atr_value)):
            continue
        if accepted_width <= 0.0 or atr_value <= 0.0:
            continue

        event_extreme = float(row["raw_high"] if direction > 0 else row["raw_low"])
        boundary = accepted_high if direction > 0 else accepted_low
        boundary_excess = direction * (event_extreme - boundary)
        if boundary_excess < config.boundary_break_atr * atr_value:
            continue

        future = joined.loc[
            (joined.index > ts)
            & (joined.index <= ts + pd.Timedelta(minutes=config.confirmation_minutes))
        ]
        if future.empty:
            continue
        observed = None
        confirm = None
        segment = None
        for obs, candidate in future.iterrows():
            close = float(candidate["raw_close"])
            reclaimed = (
                close <= accepted_high - config.reclaim_depth_atr * atr_value
                if direction > 0
                else close >= accepted_low + config.reclaim_depth_atr * atr_value
            )
            if reclaimed:
                observed = pd.Timestamp(obs)
                confirm = candidate
                segment = future.loc[:obs]
                break
        if observed is None or confirm is None or segment is None:
            continue

        opening_distance = max(abs(float(row["raw_close"]) - float(row["raw_open"])), 0.10 * atr_value)
        observed_extreme = (
            max(event_extreme, float(segment["raw_high"].max()))
            if direction > 0
            else min(event_extreme, float(segment["raw_low"].min()))
        )
        additional_extension = max(direction * (observed_extreme - event_extreme), 0.0)
        extension_ratio = additional_extension / opening_distance
        if extension_ratio > config.maximum_additional_extension_ratio:
            continue
        confirmation_close = float(confirm["raw_close"])
        failure_retracement = direction * (observed_extreme - confirmation_close) / opening_distance
        if failure_retracement < config.minimum_failure_retracement_ratio:
            continue

        confirmation_flow = direction * float(segment["signed_flow_ratio_1m"].mean())
        basis_at_event = float(row["perp_spot_log_basis"])
        basis_at_confirm = float(confirm["perp_spot_log_basis"])
        basis_decay_share = (-direction * (basis_at_confirm - basis_at_event)) / max(opening_impact, 1e-12)
        if basis_decay_share < config.minimum_basis_decay_share:
            continue
        persistent = confirmation_flow >= config.minimum_persistent_confirmation_flow
        reversed_flow = confirmation_flow <= config.maximum_reversal_confirmation_flow
        if persistent == reversed_flow:
            continue
        if persistent and config.mode not in {"PERSISTENT_FLOW_ABSORPTION", "STATE_PORTFOLIO"}:
            continue
        if reversed_flow and config.mode not in {"ACTIVE_FLOW_REVERSAL", "STATE_PORTFOLIO"}:
            continue

        side = "SELL" if direction > 0 else "BUY"
        entry = confirmation_close
        stop = observed_extreme + direction * config.stop_buffer_atr * atr_value
        target = accepted_low if direction > 0 else accepted_high
        state_name = "PERSISTENT_FLOW_ABSORPTION" if persistent else "ACTIVE_FLOW_REVERSAL"
        turnover_ratio = float(row["opening_turnover"]) / max(float(row["opening_turnover_threshold"]), 1e-12)
        depth_quality = 1.0 + max(-float(row["front_depth_change"]), 0.0)
        score = (
            float(row["opening_abs_flow"])
            * max(turnover_ratio, 1.0)
            * max(float(row["spot_participation"]), 0.0)
            * max(failure_retracement, 0.0)
            * depth_quality
            / (1.0 + extension_ratio)
        )
        appended = _append_signal(
            signals,
            config=config,
            costs=costs,
            observed=observed,
            side=side,
            entry=entry,
            stop=stop,
            target=target,
            score=score,
            source_open_time_ns=ts_ns - NS_MINUTE,
            details={
                "state": state_name,
                "event_close_utc": pd.Timestamp(ts).isoformat(),
                "confirmation_close_utc": observed.isoformat(),
                "opening_direction": direction,
                "spot_participation": float(row["spot_participation"]),
                "basis_share": float(row["basis_share"]),
                "confirmation_flow_alignment": confirmation_flow,
                "basis_decay_share": basis_decay_share,
                "front_depth_change": float(row["front_depth_change"]),
                "accepted_range_minutes": config.accepted_range_minutes,
                "accepted_close_high": accepted_high,
                "accepted_close_low": accepted_low,
                "accepted_close_width": accepted_width,
                "boundary_excess_atr": boundary_excess / atr_value,
                "additional_extension_ratio": extension_ratio,
                "failure_retracement_ratio": failure_retracement,
                "causal_interpretation": (
                    "aggressive common flow absorbed after external liquidity removal"
                    if persistent
                    else "common flow reversed after external liquidity removal"
                ),
            },
        )
        if appended:
            cooldown_until = int(observed.value) + config.cooldown_minutes * NS_MINUTE

    signals.sort(key=lambda value: (value.observed_time_ns, -value.score, value.scenario_id))
    unique: list[RotationSignal] = []
    seen_times: set[int] = set()
    for signal in signals:
        if signal.observed_time_ns in seen_times:
            continue
        seen_times.add(signal.observed_time_ns)
        unique.append(signal)
    for signal in unique:
        if signal.source_max_market_time_ns > signal.observed_time_ns:
            raise AssertionError("future information detected")
    return unique


__all__ = ["CommonFailedAuctionConfig", "build_state", "build_rotation_signals"]
