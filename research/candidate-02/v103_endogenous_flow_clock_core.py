"""Endogenous turnover-clock order-flow regimes for candidate-02 v103.

Completed aggressive quote turnover, not UTC time, closes non-overlapping
information packets.  Two consecutive packets are classified as retained price
discovery, absorbed exhaustion, or an explicit no-trade state.  This module
emits causal trade intents only; NautilusTrader owns all execution and NAV.
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
MODES = {"PORTFOLIO", "RETAINED_DISCOVERY", "ABSORBED_EXHAUSTION"}


@dataclass(frozen=True, slots=True)
class EndogenousFlowClockConfig:
    mode: str = "PORTFOLIO"
    packet_turnover_units: float = 8.0
    turnover_history_minutes: int = 1440
    turnover_minimum_minutes: int = 720
    packet_minimum_minutes: int = 2
    packet_maximum_minutes: int = 60
    packet_history_count: int = 200
    packet_minimum_history: int = 100
    first_packet_flow_quantile: float = 0.70
    first_packet_move_quantile: float = 0.60
    first_packet_efficiency_quantile: float = 0.50
    minimum_first_packet_move_atr: float = 0.15
    minimum_second_packet_flow_alignment: float = 0.05
    minimum_impact_retention: float = 0.15
    maximum_absorbed_retention: float = 0.00
    minimum_spot_confirmation_ratio: float = 0.30
    maximum_basis_expansion_share: float = 0.75
    maximum_front_depth_refill: float = 0.25
    continuation_target_packet_multiple: float = 1.25
    continuation_stop_buffer_atr: float = 0.10
    reversal_stop_buffer_atr: float = 0.10
    minimum_cost_after_rr: float = 0.80
    maximum_cost_after_rr: float = 1000.0
    cooldown_minutes: int = 30
    maximum_holding_minutes: int = 180
    atr_lookback_minutes: int = 60

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "EndogenousFlowClockConfig":
        unknown = sorted(set(values) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"unknown v103 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown v103 mode: {self.mode}")
        if not 2.0 <= self.packet_turnover_units <= 30.0:
            raise ValueError("packet_turnover_units outside structural range")
        if self.turnover_history_minutes < 720 or self.turnover_minimum_minutes < 360:
            raise ValueError("insufficient turnover-clock history")
        if not 1 <= self.packet_minimum_minutes < self.packet_maximum_minutes <= 180:
            raise ValueError("invalid packet duration range")
        if self.packet_history_count < 50 or self.packet_minimum_history < 30:
            raise ValueError("insufficient packet-regime history")
        if self.packet_minimum_history > self.packet_history_count:
            raise ValueError("packet minimum history exceeds window")
        for name in (
            "first_packet_flow_quantile",
            "first_packet_move_quantile",
            "first_packet_efficiency_quantile",
        ):
            if not 0.0 < float(getattr(self, name)) < 1.0:
                raise ValueError(f"invalid {name}")
        if self.minimum_first_packet_move_atr <= 0.0:
            raise ValueError("minimum packet move must be positive")
        if not -1.0 < self.minimum_second_packet_flow_alignment < 1.0:
            raise ValueError("invalid second packet flow floor")
        if self.minimum_impact_retention <= self.maximum_absorbed_retention:
            raise ValueError("retained and absorbed states overlap")
        if not 0.0 <= self.minimum_spot_confirmation_ratio <= 2.0:
            raise ValueError("invalid spot confirmation ratio")
        if not 0.0 <= self.maximum_basis_expansion_share <= 2.0:
            raise ValueError("invalid basis expansion share")
        if self.maximum_front_depth_refill < -1.0:
            raise ValueError("invalid depth refill ceiling")
        if self.continuation_target_packet_multiple <= 0.0:
            raise ValueError("invalid continuation target")
        if self.continuation_stop_buffer_atr < 0.0 or self.reversal_stop_buffer_atr < 0.0:
            raise ValueError("negative stop buffer")
        if not 0.0 < self.minimum_cost_after_rr <= self.maximum_cost_after_rr:
            raise ValueError("invalid cost-after reward/risk band")
        if self.cooldown_minutes < 0 or self.maximum_holding_minutes <= 0:
            raise ValueError("invalid timing")
        if self.atr_lookback_minutes < 30:
            raise ValueError("ATR lookback too short")


@dataclass(frozen=True, slots=True)
class FlowPacket:
    start_position: int
    end_position: int
    start_time: pd.Timestamp
    end_time: pd.Timestamp
    duration_minutes: int
    turnover: float
    signed_flow_ratio: float
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    log_return: float
    move_atr: float
    path_efficiency: float
    spot_log_return: float
    spot_signed_flow_ratio: float
    basis_start: float
    basis_end: float
    front_depth_change: float
    atr: float
    vpin: float


def _normalise_index(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy().sort_index()
    result.index = (
        result.index.tz_localize(UTC)
        if result.index.tz is None
        else result.index.tz_convert(UTC)
    )
    if result.index.has_duplicates or not result.index.is_monotonic_increasing:
        raise ValueError("timestamps must be unique and increasing")
    return result


def _normalise_timestamp(value: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize(UTC) if ts.tzinfo is None else ts.tz_convert(UTC)


def _finite(values: Sequence[float]) -> bool:
    return all(math.isfinite(float(value)) for value in values)


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


def build_state(features: pd.DataFrame, config: EndogenousFlowClockConfig) -> pd.DataFrame:
    required = {
        "close",
        "aggressive_signed_quote_1m",
        "aggressive_total_quote_1m",
        "ask_depth_1pct_end",
        "bid_depth_1pct_end",
        "spot_open",
        "spot_close",
        "spot_aggressive_signed_quote_1m",
        "spot_aggressive_total_quote_1m",
        "perp_spot_log_basis",
        "vpin_50",
    }
    missing = sorted(required - set(features.columns))
    if missing:
        raise ValueError(f"v103 missing completed-minute features: {missing}")
    x = _normalise_index(features)
    x["packet_turnover_base"] = (
        x["aggressive_total_quote_1m"]
        .rolling(
            config.turnover_history_minutes,
            min_periods=config.turnover_minimum_minutes,
        )
        .median()
        .shift(1)
    )
    return x


def _build_packets(x: pd.DataFrame, config: EndogenousFlowClockConfig) -> list[FlowPacket]:
    packets: list[FlowPacket] = []
    position = 0
    while position < len(x):
        base = float(x["packet_turnover_base"].iloc[position])
        if not math.isfinite(base) or base <= 0.0:
            position += 1
            continue
        target_turnover = base * config.packet_turnover_units
        end = position
        accumulated = 0.0
        while (
            end < len(x)
            and accumulated < target_turnover
            and end - position < config.packet_maximum_minutes
        ):
            value = float(x["aggressive_total_quote_1m"].iloc[end])
            if math.isfinite(value) and value >= 0.0:
                accumulated += value
            end += 1
        duration = end - position
        if accumulated < target_turnover:
            position = max(end, position + 1)
            continue
        if duration < config.packet_minimum_minutes:
            position = end
            continue

        segment = x.iloc[position:end]
        required = (
            "raw_open",
            "raw_high",
            "raw_low",
            "raw_close",
            "atr",
            "aggressive_total_quote_1m",
            "aggressive_signed_quote_1m",
            "spot_open",
            "spot_close",
            "spot_aggressive_total_quote_1m",
            "spot_aggressive_signed_quote_1m",
            "perp_spot_log_basis",
            "ask_depth_1pct_end",
            "bid_depth_1pct_end",
            "vpin_50",
        )
        if segment[list(required)].replace([np.inf, -np.inf], np.nan).isna().any().any():
            position = end
            continue

        turnover = float(segment["aggressive_total_quote_1m"].sum())
        signed = float(segment["aggressive_signed_quote_1m"].sum())
        spot_turnover = float(segment["spot_aggressive_total_quote_1m"].sum())
        spot_signed = float(segment["spot_aggressive_signed_quote_1m"].sum())
        open_price = float(segment["raw_open"].iloc[0])
        close_price = float(segment["raw_close"].iloc[-1])
        spot_open = float(segment["spot_open"].iloc[0])
        spot_close = float(segment["spot_close"].iloc[-1])
        atr = float(segment["atr"].iloc[-1])
        if not _finite(
            (
                turnover,
                signed,
                spot_turnover,
                spot_signed,
                open_price,
                close_price,
                spot_open,
                spot_close,
                atr,
            )
        ):
            position = end
            continue
        if (
            turnover <= 0.0
            or spot_turnover <= 0.0
            or open_price <= 0.0
            or close_price <= 0.0
            or spot_open <= 0.0
            or spot_close <= 0.0
            or atr <= 0.0
        ):
            position = end
            continue

        signed_flow_ratio = signed / turnover
        spot_signed_flow_ratio = spot_signed / spot_turnover
        log_return = math.log(close_price / open_price)
        spot_log_return = math.log(spot_close / spot_open)
        closes = segment["raw_close"].to_numpy(dtype=float)
        path = float(np.abs(np.diff(np.concatenate(([open_price], closes)))).sum())
        efficiency = abs(close_price - open_price) / path if path > 0.0 else 0.0
        direction = int(np.sign(signed_flow_ratio))
        if direction > 0:
            depth_start = float(segment["ask_depth_1pct_end"].iloc[0])
            depth_end = float(segment["ask_depth_1pct_end"].iloc[-1])
        elif direction < 0:
            depth_start = float(segment["bid_depth_1pct_end"].iloc[0])
            depth_end = float(segment["bid_depth_1pct_end"].iloc[-1])
        else:
            depth_start = depth_end = math.nan
        front_depth_change = (
            (depth_end - depth_start) / max(abs(depth_start), 1e-12)
            if math.isfinite(depth_start) and math.isfinite(depth_end)
            else math.nan
        )

        packets.append(
            FlowPacket(
                start_position=position,
                end_position=end - 1,
                start_time=pd.Timestamp(segment.index[0]),
                end_time=pd.Timestamp(segment.index[-1]),
                duration_minutes=duration,
                turnover=turnover,
                signed_flow_ratio=signed_flow_ratio,
                open_price=open_price,
                high_price=float(segment["raw_high"].max()),
                low_price=float(segment["raw_low"].min()),
                close_price=close_price,
                log_return=log_return,
                move_atr=abs(close_price - open_price) / atr,
                path_efficiency=efficiency,
                spot_log_return=spot_log_return,
                spot_signed_flow_ratio=spot_signed_flow_ratio,
                basis_start=float(segment["perp_spot_log_basis"].iloc[0]),
                basis_end=float(segment["perp_spot_log_basis"].iloc[-1]),
                front_depth_change=front_depth_change,
                atr=atr,
                vpin=float(segment["vpin_50"].iloc[-1]),
            )
        )
        position = end
    return packets


def _prior_thresholds(
    packets: Sequence[FlowPacket],
    index: int,
    config: EndogenousFlowClockConfig,
) -> tuple[float, float, float] | None:
    history = packets[max(0, index - config.packet_history_count) : index]
    if len(history) < config.packet_minimum_history:
        return None
    flows = np.asarray([abs(packet.signed_flow_ratio) for packet in history], dtype=float)
    moves = np.asarray([packet.move_atr for packet in history], dtype=float)
    efficiencies = np.asarray([packet.path_efficiency for packet in history], dtype=float)
    if not (np.isfinite(flows).all() and np.isfinite(moves).all() and np.isfinite(efficiencies).all()):
        return None
    return (
        float(np.quantile(flows, config.first_packet_flow_quantile)),
        float(np.quantile(moves, config.first_packet_move_quantile)),
        float(np.quantile(efficiencies, config.first_packet_efficiency_quantile)),
    )


def _mode_enabled(config: EndogenousFlowClockConfig, mode: str) -> bool:
    return config.mode == "PORTFOLIO" or config.mode == mode


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: EndogenousFlowClockConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = _normalise_timestamp(evaluation_start)
    end = _normalise_timestamp(evaluation_end)
    if end <= start:
        raise ValueError("evaluation end must be after start")

    raw_view = _normalise_index(raw[["open", "high", "low", "close"]])
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
    x["atr"] = (
        _true_range(raw_view)
        .rolling(
            config.atr_lookback_minutes,
            min_periods=max(30, config.atr_lookback_minutes // 2),
        )
        .median()
        .shift(1)
        .reindex(x.index)
    )
    packets = _build_packets(x, config)
    signals: list[RotationSignal] = []
    cooldown_until = -1

    for index in range(1, len(packets)):
        first = packets[index - 1]
        second = packets[index]
        if first.end_time < start or second.end_time >= end:
            continue
        thresholds = _prior_thresholds(packets, index - 1, config)
        if thresholds is None:
            continue
        flow_threshold, move_threshold, efficiency_threshold = thresholds
        direction = int(np.sign(first.signed_flow_ratio))
        if direction == 0:
            continue
        if abs(first.signed_flow_ratio) < flow_threshold:
            continue
        if first.move_atr < max(move_threshold, config.minimum_first_packet_move_atr):
            continue
        if first.path_efficiency < efficiency_threshold:
            continue
        if direction * first.log_return <= 0.0:
            continue
        if direction * second.signed_flow_ratio < config.minimum_second_packet_flow_alignment:
            continue

        first_move = abs(first.close_price - first.open_price)
        if first_move <= 0.0:
            continue
        retention = direction * (second.close_price - first.close_price) / first_move
        total_perp_return = direction * (first.log_return + second.log_return)
        total_spot_return = direction * (first.spot_log_return + second.spot_log_return)
        if total_perp_return <= 0.0:
            spot_ratio = -math.inf
            basis_share = math.inf
        else:
            spot_ratio = total_spot_return / total_perp_return
            basis_expansion = max(direction * (second.basis_end - first.basis_start), 0.0)
            basis_share = basis_expansion / max(total_perp_return, 1e-12)
        depth_refill = max(
            first.front_depth_change if math.isfinite(first.front_depth_change) else 0.0,
            second.front_depth_change if math.isfinite(second.front_depth_change) else 0.0,
        )

        continuation = (
            _mode_enabled(config, "RETAINED_DISCOVERY")
            and retention >= config.minimum_impact_retention
            and direction * second.log_return > 0.0
            and spot_ratio >= config.minimum_spot_confirmation_ratio
            and basis_share <= config.maximum_basis_expansion_share
            and depth_refill <= config.maximum_front_depth_refill
        )
        midpoint = 0.5 * (first.open_price + first.close_price)
        reclaimed_midpoint = (
            second.close_price < midpoint
            if direction > 0
            else second.close_price > midpoint
        )
        reversal = (
            _mode_enabled(config, "ABSORBED_EXHAUSTION")
            and retention <= config.maximum_absorbed_retention
            and reclaimed_midpoint
            and (
                spot_ratio < config.minimum_spot_confirmation_ratio
                or basis_share > config.maximum_basis_expansion_share
            )
        )
        if continuation == reversal:
            continue

        observed_ns = int(second.end_time.value)
        if observed_ns <= cooldown_until:
            continue
        side_direction = direction if continuation else -direction
        side = "BUY" if side_direction > 0 else "SELL"
        entry = second.close_price
        if continuation:
            stop = (
                second.low_price - config.continuation_stop_buffer_atr * second.atr
                if side_direction > 0
                else second.high_price + config.continuation_stop_buffer_atr * second.atr
            )
            target = (
                entry
                + side_direction
                * config.continuation_target_packet_multiple
                * first_move
            )
            state_name = "RETAINED_IMPACT_PRICE_DISCOVERY"
        else:
            stop = (
                min(first.low_price, second.low_price)
                - config.reversal_stop_buffer_atr * second.atr
                if side_direction > 0
                else max(first.high_price, second.high_price)
                + config.reversal_stop_buffer_atr * second.atr
            )
            target = first.open_price
            state_name = "ABSORBED_FLOW_EXHAUSTION"

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
        if not math.isfinite(rr) or not (
            config.minimum_cost_after_rr <= rr <= config.maximum_cost_after_rr
        ):
            continue

        score = (
            rr
            * max(abs(first.signed_flow_ratio) / max(flow_threshold, 1e-12), 1.0)
            * max(first.move_atr / max(move_threshold, 1e-12), 1.0)
            * (1.0 + abs(retention))
        )
        details = {
            "state": state_name,
            "clock": "NON_OVERLAPPING_AGGRESSIVE_QUOTE_TURNOVER",
            "packet_turnover_units": config.packet_turnover_units,
            "first_packet_start_utc": first.start_time.isoformat(),
            "first_packet_end_utc": first.end_time.isoformat(),
            "second_packet_start_utc": second.start_time.isoformat(),
            "second_packet_end_utc": second.end_time.isoformat(),
            "first_packet_duration_minutes": first.duration_minutes,
            "second_packet_duration_minutes": second.duration_minutes,
            "first_packet_flow_ratio": first.signed_flow_ratio,
            "second_packet_flow_ratio": second.signed_flow_ratio,
            "first_packet_move_atr": first.move_atr,
            "first_packet_path_efficiency": first.path_efficiency,
            "impact_retention": retention,
            "spot_confirmation_ratio": spot_ratio,
            "basis_expansion_share": basis_share,
            "front_depth_refill": depth_refill,
            "first_packet_origin": first.open_price,
            "selected_cost_after_rr": rr,
            "causal_interpretation": (
                "persistent split-order flow retained price impact and spot participation"
                if continuation
                else "aggressive flow persisted while price impact decayed and the packet midpoint was reclaimed"
            ),
        }
        signals.append(
            RotationSignal(
                scenario_id=f"v103-{state_name.lower()}-{observed_ns}",
                observed_time_ns=observed_ns,
                side=side,
                entry_reference=entry,
                stop_price=stop,
                target_price=target,
                cost_after_reward_risk=rr,
                score=float(score),
                max_hold_minutes=config.maximum_holding_minutes,
                source_feature_open_time_ns=int(first.start_time.value) - NS_MINUTE,
                source_feature_available_time_ns=observed_ns,
                source_max_market_time_ns=observed_ns,
                details=details,
            )
        )
        cooldown_until = observed_ns + config.cooldown_minutes * NS_MINUTE

    signals.sort(key=lambda signal: (signal.observed_time_ns, -signal.score, signal.scenario_id))
    unique: list[RotationSignal] = []
    seen: set[int] = set()
    for signal in signals:
        if signal.observed_time_ns in seen:
            continue
        if signal.source_max_market_time_ns > signal.observed_time_ns:
            raise AssertionError("future information detected in v103")
        seen.add(signal.observed_time_ns)
        unique.append(signal)
    return unique


__all__ = ["EndogenousFlowClockConfig", "build_state", "build_rotation_signals"]
