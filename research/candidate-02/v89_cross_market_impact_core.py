"""Cross-market impact permanence state machine for candidate-02 v89.

The module converts only completed futures/spot observations into deterministic
trade intents.  NautilusTrader owns all orders, fills, fees, positions and NAV.

Economic state:
- A quarter-hour perpetual-futures order-flow burst is merely an event clock.
- Common spot participation plus retained cross-market impact identifies
  informational price discovery.
- Perpetual-specific basis expansion plus rapid impact decay identifies a
  transient derivative overshoot.
- Ambiguous events are explicit no-trade states.
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
MODES = {"PERMANENT_DISCOVERY", "TRANSIENT_OVERSHOOT", "STATE_PORTFOLIO"}


@dataclass(frozen=True, slots=True)
class CrossMarketImpactConfig:
    mode: str = "STATE_PORTFOLIO"
    prior_days: int = 2
    prior_minimum_events: int = 64
    opening_flow_abs_quantile: float = 0.65
    opening_turnover_quantile: float = 0.55
    opening_abs_return_quantile: float = 0.55
    minimum_opening_flow_ratio: float = 0.08
    confirmation_minutes: int = 2
    minimum_common_spot_participation: float = 0.35
    minimum_common_spot_flow_alignment: float = 0.00
    maximum_common_basis_share: float = 0.65
    minimum_common_impact_retention: float = 0.45
    maximum_transient_spot_participation: float = 0.20
    minimum_transient_basis_share: float = 0.35
    maximum_transient_impact_retention: float = 0.30
    maximum_transient_confirmation_flow: float = 0.00
    accepted_range_minutes: int = 30
    stop_buffer_atr: float = 0.10
    target_range_multiple: float = 1.00
    atr_lookback_minutes: int = 60
    cooldown_minutes: int = 15
    maximum_holding_minutes: int = 180
    minimum_cost_after_rr: float = 0.80
    maximum_cost_after_rr: float = 5.00

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "CrossMarketImpactConfig":
        unknown = sorted(set(values) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"unknown v89 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown v89 mode: {self.mode}")
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
        if self.confirmation_minutes not in {1, 2, 3}:
            raise ValueError("confirmation_minutes must be 1, 2 or 3")
        if self.accepted_range_minutes not in {15, 30, 60}:
            raise ValueError("accepted_range_minutes must be 15, 30 or 60")
        if not 0.0 < self.minimum_opening_flow_ratio < 1.0:
            raise ValueError("invalid opening flow floor")
        if not 0.0 <= self.maximum_transient_spot_participation < self.minimum_common_spot_participation:
            raise ValueError("spot-participation states must remain separated")
        if not 0.0 <= self.maximum_transient_impact_retention < self.minimum_common_impact_retention:
            raise ValueError("impact-retention states must remain separated")
        if self.atr_lookback_minutes < 30 or self.maximum_holding_minutes <= 0:
            raise ValueError("invalid horizon")
        if self.stop_buffer_atr < 0.0 or self.target_range_multiple <= 0.0:
            raise ValueError("invalid price geometry")
        if not 0.0 < self.minimum_cost_after_rr <= self.maximum_cost_after_rr:
            raise ValueError("invalid cost-after reward/risk band")


def _quarter_hour_mask(index: pd.DatetimeIndex) -> np.ndarray:
    opening_time = index - pd.Timedelta(minutes=1)
    return (opening_time.minute % 15 == 0)


def _prior_event_quantile(
    series: pd.Series,
    mask: np.ndarray,
    *,
    days: int,
    quantile: float,
    minimum_events: int,
) -> pd.Series:
    event_series = series.where(mask)
    return event_series.shift(1).rolling(
        days * 24 * 60,
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


def build_state(features: pd.DataFrame, config: CrossMarketImpactConfig) -> pd.DataFrame:
    required = {
        "close",
        "signed_flow_ratio_1m",
        "ask_depth_change_1m",
        "bid_depth_change_1m",
        "qh_opening_10s_flow_ratio",
        "qh_opening_10s_total_quote",
        "qh_opening_10s_abs_return",
        "qh_opening_10s_return",
        "qh_rest_50s_flow_ratio",
        "qh_full_minute_return",
        "spot_open",
        "spot_close",
        "spot_signed_flow_ratio_1m",
        "spot_qh_opening_10s_flow_ratio",
        "spot_qh_opening_10s_return",
        "spot_qh_rest_50s_flow_ratio",
        "spot_qh_full_minute_return",
        "perp_spot_log_basis",
    }
    missing = sorted(required - set(features.columns))
    if missing:
        raise ValueError(f"v89 cross-market feature columns missing: {missing}")

    x = features.copy()
    mask = _quarter_hour_mask(x.index)
    x["is_quarter_hour_open"] = mask.astype(float)
    x["opening_direction"] = np.sign(x["qh_opening_10s_flow_ratio"])
    x["opening_abs_flow"] = x["qh_opening_10s_flow_ratio"].abs()
    x["opening_turnover"] = x["qh_opening_10s_total_quote"]
    x["opening_abs_return"] = x["qh_opening_10s_abs_return"]
    x["perp_open_alignment"] = x["opening_direction"] * x["qh_full_minute_return"]
    x["spot_open_alignment"] = x["opening_direction"] * x["spot_qh_full_minute_return"]
    x["spot_flow_alignment"] = x["opening_direction"] * x["spot_signed_flow_ratio_1m"]
    x["perp_rest_flow_alignment"] = x["opening_direction"] * x["qh_rest_50s_flow_ratio"]
    x["spot_rest_flow_alignment"] = x["opening_direction"] * x["spot_qh_rest_50s_flow_ratio"]
    x["front_depth_change"] = np.where(
        x["opening_direction"] > 0,
        x["ask_depth_change_1m"],
        x["bid_depth_change_1m"],
    )
    denominator = x["perp_open_alignment"].where(x["perp_open_alignment"] > 1e-12)
    x["spot_participation"] = x["spot_open_alignment"] / denominator
    x["basis_share"] = (
        x["opening_direction"]
        * (x["qh_full_minute_return"] - x["spot_qh_full_minute_return"])
        / denominator
    )
    x["opening_flow_threshold"] = _prior_event_quantile(
        x["opening_abs_flow"], mask,
        days=config.prior_days,
        quantile=config.opening_flow_abs_quantile,
        minimum_events=config.prior_minimum_events,
    )
    x["opening_turnover_threshold"] = _prior_event_quantile(
        x["opening_turnover"], mask,
        days=config.prior_days,
        quantile=config.opening_turnover_quantile,
        minimum_events=config.prior_minimum_events,
    )
    x["opening_return_threshold"] = _prior_event_quantile(
        x["opening_abs_return"], mask,
        days=config.prior_days,
        quantile=config.opening_abs_return_quantile,
        minimum_events=config.prior_minimum_events,
    )
    return x


def _finite(row: pd.Series, names: tuple[str, ...]) -> bool:
    return all(math.isfinite(float(row[name])) for name in names)


def _append_signal(
    output: list[RotationSignal],
    *,
    config: CrossMarketImpactConfig,
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
            scenario_id=f"v89-{str(details['state']).lower()}-{observed_ns}",
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
    config: CrossMarketImpactConfig,
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
    base_fields = (
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
        "spot_open",
        "raw_open",
        "raw_high",
        "raw_low",
        "raw_close",
    )

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
        opening_impact = float(row["perp_open_alignment"])
        if opening_impact <= 0.0:
            continue

        formation = raw.loc[
            (raw.index >= ts - pd.Timedelta(minutes=config.accepted_range_minutes))
            & (raw.index <= ts - pd.Timedelta(minutes=1))
        ]
        if len(formation) < config.accepted_range_minutes - 1:
            continue
        accepted_high = float(formation["close"].max())
        accepted_low = float(formation["close"].min())
        accepted_mid = 0.5 * (accepted_high + accepted_low)
        accepted_width = accepted_high - accepted_low
        atr_value = float(atr.asof(ts))
        if not all(math.isfinite(v) for v in (accepted_high, accepted_low, accepted_mid, accepted_width, atr_value)):
            continue
        if accepted_width <= 0.0 or atr_value <= 0.0:
            continue

        future = joined.loc[(joined.index > ts) & (joined.index <= ts + pd.Timedelta(minutes=config.confirmation_minutes))]
        if len(future) < config.confirmation_minutes:
            continue
        obs = future.index[-1]
        confirm = future.iloc[-1]
        event_perp_open = float(row["raw_open"])
        event_spot_open = float(row["spot_open"])
        if event_perp_open <= 0.0 or event_spot_open <= 0.0:
            continue
        cumulative_perp = direction * math.log(float(confirm["raw_close"]) / event_perp_open)
        cumulative_spot = direction * math.log(float(confirm["spot_close"]) / event_spot_open)
        impact_retention = cumulative_perp / max(opening_impact, 1e-12)
        confirmation_flow = direction * float(future["signed_flow_ratio_1m"].mean())
        basis_at_event = float(row["perp_spot_log_basis"])
        basis_at_confirm = float(confirm["perp_spot_log_basis"])
        basis_decay = -direction * (basis_at_confirm - basis_at_event)
        spot_participation = float(row["spot_participation"])
        basis_share = float(row["basis_share"])
        front_depth = float(row["front_depth_change"])
        turnover_ratio = float(row["opening_turnover"]) / max(float(row["opening_turnover_threshold"]), 1e-12)
        depth_quality = 1.0 + max(-front_depth, 0.0)

        common = (
            spot_participation >= config.minimum_common_spot_participation
            and float(row["spot_flow_alignment"]) >= config.minimum_common_spot_flow_alignment
            and basis_share <= config.maximum_common_basis_share
            and impact_retention >= config.minimum_common_impact_retention
            and cumulative_spot > 0.0
            and confirmation_flow >= -0.05
        )
        transient = (
            spot_participation <= config.maximum_transient_spot_participation
            and basis_share >= config.minimum_transient_basis_share
            and impact_retention <= config.maximum_transient_impact_retention
            and confirmation_flow <= config.maximum_transient_confirmation_flow
            and basis_decay > 0.0
        )
        # Explicit ambiguous state; a single event can never be both scenarios.
        if common == transient:
            continue

        source_open_ns = ts_ns - NS_MINUTE
        if common and config.mode in {"PERMANENT_DISCOVERY", "STATE_PORTFOLIO"}:
            side = "BUY" if direction > 0 else "SELL"
            entry = float(confirm["raw_close"])
            stop = event_perp_open - direction * config.stop_buffer_atr * atr_value
            boundary = accepted_high if direction > 0 else accepted_low
            target = boundary + direction * config.target_range_multiple * accepted_width
            score = float(row["opening_abs_flow"]) * max(turnover_ratio, 1.0) * max(spot_participation, 0.0) * depth_quality
            appended = _append_signal(
                signals,
                config=config,
                costs=costs,
                observed=pd.Timestamp(obs),
                side=side,
                entry=entry,
                stop=stop,
                target=target,
                score=score,
                source_open_time_ns=source_open_ns,
                details={
                    "state": "PERMANENT_DISCOVERY",
                    "event_close_utc": pd.Timestamp(ts).isoformat(),
                    "confirmation_close_utc": pd.Timestamp(obs).isoformat(),
                    "spot_participation": spot_participation,
                    "basis_share": basis_share,
                    "impact_retention": impact_retention,
                    "basis_decay": basis_decay,
                    "confirmation_flow_alignment": confirmation_flow,
                    "front_depth_change": front_depth,
                    "accepted_range_minutes": config.accepted_range_minutes,
                    "accepted_close_high": accepted_high,
                    "accepted_close_low": accepted_low,
                    "accepted_close_mid": accepted_mid,
                    "accepted_close_width": accepted_width,
                    "causal_interpretation": "common spot-perpetual information with retained impact",
                },
            )
        elif transient and config.mode in {"TRANSIENT_OVERSHOOT", "STATE_PORTFOLIO"}:
            side = "SELL" if direction > 0 else "BUY"
            entry = float(confirm["raw_close"])
            event_extreme = float(row["raw_high"] if direction > 0 else row["raw_low"])
            stop = event_extreme + direction * config.stop_buffer_atr * atr_value
            target = accepted_mid
            score = float(row["opening_abs_flow"]) * max(turnover_ratio, 1.0) * max(basis_share, 0.0) * max(basis_decay / max(opening_impact, 1e-12), 0.0)
            appended = _append_signal(
                signals,
                config=config,
                costs=costs,
                observed=pd.Timestamp(obs),
                side=side,
                entry=entry,
                stop=stop,
                target=target,
                score=score,
                source_open_time_ns=source_open_ns,
                details={
                    "state": "TRANSIENT_OVERSHOOT",
                    "event_close_utc": pd.Timestamp(ts).isoformat(),
                    "confirmation_close_utc": pd.Timestamp(obs).isoformat(),
                    "spot_participation": spot_participation,
                    "basis_share": basis_share,
                    "impact_retention": impact_retention,
                    "basis_decay": basis_decay,
                    "confirmation_flow_alignment": confirmation_flow,
                    "front_depth_change": front_depth,
                    "accepted_range_minutes": config.accepted_range_minutes,
                    "accepted_close_high": accepted_high,
                    "accepted_close_low": accepted_low,
                    "accepted_close_mid": accepted_mid,
                    "accepted_close_width": accepted_width,
                    "causal_interpretation": "perpetual-specific dislocation with decaying mechanical impact",
                },
            )
        else:
            appended = False

        if appended:
            cooldown_until = int(obs.value) + config.cooldown_minutes * NS_MINUTE

    signals.sort(key=lambda value: (value.observed_time_ns, -value.score, value.scenario_id))
    # The state definitions are mutually exclusive, but protect the global one-signal schedule anyway.
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


__all__ = ["CrossMarketImpactConfig", "build_state", "build_rotation_signals"]
