"""Candidate-02 V156: failed informed-inventory auction reversal.

V155 proved that price/OI/top-position/taker-flow accumulation is not by itself
continuation alpha. V156 preserves that accumulation only as context. It enters
only after the accumulated inventory fails: price re-enters the buildup range,
aggressive flow flips, and OI/top-position support stops reinforcing. The
opposite edge of the completed buildup range is the pre-observed objective.

This module creates causal trade intents only. NautilusTrader owns orders,
fills, fees, positions, margin, and NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from v53_nt_core import CostConfig, RotationSignal, cost_after_reward_risk
from v155_informed_inventory_core import (
    InformedInventoryConfig,
    build_informed_inventory_state,
    informed_inventory_candidate_mask,
    load_metrics,
    load_raw_one_minute,
)

NS_MINUTE = 60_000_000_000


@dataclass(frozen=True, slots=True)
class InventoryFailureConfig:
    observation_bars: int = 6
    minimum_price_move_atr: float = 0.50
    maximum_price_move_atr: float = 5.00
    atr_history_bars: int = 48
    robust_history_bars: int = 288
    robust_minimum_observations: int = 96
    robust_scale_constant: float = 1.4826
    minimum_oi_z: float = 1.00
    minimum_top_position_directional_z: float = 1.00
    minimum_top_account_directional_z: float = -2.00
    broad_herding_rejection_z: float = 1.00
    minimum_taker_directional_z: float = 0.00
    minimum_failure_delay_bars: int = 1
    maximum_failure_wait_bars: int = 18
    reentry_fraction: float = 0.50
    require_oi_nonreinforcement: bool = True
    require_top_position_nonreinforcement: bool = True
    stop_buffer_atr: float = 0.15
    minimum_cost_after_reward_risk: float = 0.75
    maximum_holding_minutes: int = 180
    episode_cooldown_minutes: int = 90

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "InventoryFailureConfig":
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"unknown inventory-failure config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        if self.observation_bars != 6:
            raise ValueError("V156 keeps the locked six-bar buildup context")
        if self.atr_history_bars != 48:
            raise ValueError("V156 keeps the locked 48-bar prior ATR")
        if self.robust_history_bars != 288 or self.robust_minimum_observations != 96:
            raise ValueError("V156 keeps the locked robust reference window")
        if not 0.0 < self.reentry_fraction < 1.0:
            raise ValueError("reentry_fraction must be inside the buildup range")
        if not 0 <= self.minimum_failure_delay_bars <= self.maximum_failure_wait_bars:
            raise ValueError("invalid failure search window")
        if self.stop_buffer_atr < 0 or self.minimum_cost_after_reward_risk <= 0:
            raise ValueError("invalid geometry constraints")
        if self.maximum_holding_minutes <= 0 or self.episode_cooldown_minutes <= 0:
            raise ValueError("holding and cooldown must be positive")

    def context(self) -> InformedInventoryConfig:
        return InformedInventoryConfig(
            observation_bars=self.observation_bars,
            minimum_price_move_atr=self.minimum_price_move_atr,
            maximum_price_move_atr=self.maximum_price_move_atr,
            atr_history_bars=self.atr_history_bars,
            robust_history_bars=self.robust_history_bars,
            robust_minimum_observations=self.robust_minimum_observations,
            robust_scale_constant=self.robust_scale_constant,
            minimum_oi_z=self.minimum_oi_z,
            minimum_top_position_directional_z=self.minimum_top_position_directional_z,
            minimum_top_account_directional_z=self.minimum_top_account_directional_z,
            broad_herding_rejection_z=self.broad_herding_rejection_z,
            minimum_taker_directional_z=self.minimum_taker_directional_z,
            stop_buffer_atr=self.stop_buffer_atr,
            cost_after_target_r=0.75,
            maximum_holding_minutes=self.maximum_holding_minutes,
        )


def build_inventory_failure_state(
    *,
    raw_one_minute: pd.DataFrame,
    metrics: pd.DataFrame,
    config: InventoryFailureConfig,
) -> pd.DataFrame:
    state = build_informed_inventory_state(
        raw_one_minute=raw_one_minute,
        metrics=metrics,
        config=config.context(),
    )
    for source, name in (
        ("sum_open_interest", "oi_one_bar_change"),
        ("sum_toptrader_long_short_ratio", "top_position_one_bar_change"),
        ("sum_taker_long_short_vol_ratio", "taker_one_bar_change"),
    ):
        values = state[source].where(state[source] > 0.0)
        state[name] = np.log(values / values.shift(1))
    return state


def _utc(value: pd.Timestamp | str) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def build_inventory_failure_signals(
    *,
    state: pd.DataFrame,
    raw_one_minute: pd.DataFrame,
    evaluation_start: pd.Timestamp | str,
    evaluation_end: pd.Timestamp | str,
    config: InventoryFailureConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start, end = _utc(evaluation_start), _utc(evaluation_end)
    if end <= start:
        raise ValueError("evaluation_end must be after evaluation_start")
    context_config = config.context()
    context_mask = informed_inventory_candidate_mask(state, context_config)
    contexts = state.loc[context_mask & (state.index >= start) & (state.index < end)]
    signals: list[RotationSignal] = []
    next_episode_allowed = start

    for context_time, context in contexts.iterrows():
        if context_time < next_episode_allowed:
            continue
        direction = int(context["price_direction"])
        if direction not in (-1, 1):
            continue
        atr = float(context["prior_atr"])
        high = float(context["observation_high"])
        low = float(context["observation_low"])
        if not all(math.isfinite(value) for value in (atr, high, low)) or atr <= 0 or high <= low:
            continue
        context_oi = float(context["sum_open_interest"])
        context_top = float(context["sum_toptrader_long_short_ratio"])
        range_level = low + config.reentry_fraction * (high - low)
        candidates = state.loc[
            (state.index > context_time)
            & (state.index <= context_time + pd.Timedelta(minutes=5 * config.maximum_failure_wait_bars))
            & (state.index < end)
        ]
        emitted = False
        for step, (failure_time, row) in enumerate(candidates.iterrows(), start=1):
            if step <= config.minimum_failure_delay_bars:
                continue
            close = float(row["close"])
            opposite_body = int(row["current_candle_direction"]) == -direction
            price_reentry = close <= range_level if direction > 0 else close >= range_level
            taker_flip = (-direction * float(row["taker_one_bar_change"])) > 0.0
            oi_nonreinforcement = (
                float(row["sum_open_interest"]) <= context_oi
                or float(row["oi_one_bar_change"]) < 0.0
            )
            top_nonreinforcement = (
                direction * math.log(float(row["sum_toptrader_long_short_ratio"]) / context_top) <= 0.0
                or direction * float(row["top_position_one_bar_change"]) < 0.0
            )
            if not (price_reentry and opposite_body and taker_flip):
                continue
            if config.require_oi_nonreinforcement and not oi_nonreinforcement:
                continue
            if config.require_top_position_nonreinforcement and not top_nonreinforcement:
                continue

            activation_time = failure_time + pd.Timedelta(minutes=1)
            if activation_time >= end or activation_time not in raw_one_minute.index:
                continue
            entry = float(raw_one_minute.at[activation_time, "close"])
            side = "SELL" if direction > 0 else "BUY"
            stop = high + config.stop_buffer_atr * atr if side == "SELL" else low - config.stop_buffer_atr * atr
            target = low if side == "SELL" else high
            geometry = target < entry < stop if side == "SELL" else stop < entry < target
            if stop <= 0 or target <= 0 or not geometry:
                continue
            rr = cost_after_reward_risk(
                entry=entry,
                stop=stop,
                target=target,
                side=side,
                costs=costs,
            )
            if not math.isfinite(rr) or rr < config.minimum_cost_after_reward_risk:
                continue
            observed_ns = int(activation_time.value)
            failure_ns = int(failure_time.value)
            context_ns = int(context_time.value)
            signals.append(RotationSignal(
                scenario_id=f"v156-inventory-failure-{context_ns}-{observed_ns}",
                observed_time_ns=observed_ns,
                side=side,
                entry_reference=entry,
                stop_price=float(stop),
                target_price=float(target),
                cost_after_reward_risk=float(rr),
                score=1.0,
                max_hold_minutes=config.maximum_holding_minutes,
                source_feature_open_time_ns=failure_ns - 5 * NS_MINUTE,
                source_feature_available_time_ns=observed_ns,
                source_max_market_time_ns=failure_ns,
                details={
                    "module": "FAILED_INFORMED_INVENTORY_AUCTION_REVERSAL",
                    "lineage": "candidate-02-v155-context-reused-not-inverted",
                    "state_sequence": [
                        "INVENTORY_BUILDUP_CONTEXT",
                        "RANGE_REENTRY",
                        "AGGRESSOR_FLOW_FLIP",
                        "INVENTORY_NONREINFORCEMENT",
                        "REVERSAL_ENTRY_ARMED",
                    ],
                    "context_time_ns": context_ns,
                    "failure_time_ns": failure_ns,
                    "activation_time_ns": observed_ns,
                    "context_direction": direction,
                    "observation_high": high,
                    "observation_low": low,
                    "range_reentry_level": range_level,
                    "context_oi": context_oi,
                    "failure_oi": float(row["sum_open_interest"]),
                    "context_top_position_ratio": context_top,
                    "failure_top_position_ratio": float(row["sum_toptrader_long_short_ratio"]),
                    "taker_one_bar_change": float(row["taker_one_bar_change"]),
                    "oi_one_bar_change": float(row["oi_one_bar_change"]),
                    "top_position_one_bar_change": float(row["top_position_one_bar_change"]),
                    "prior_atr": atr,
                    "natural_target": "OPPOSITE_EDGE_OF_COMPLETED_BUILDUP_RANGE",
                    "minimum_cost_after_reward_risk": config.minimum_cost_after_reward_risk,
                    "score_affects_risk": False,
                    "future_information_used": False,
                },
            ))
            emitted = True
            break
        next_episode_allowed = context_time + pd.Timedelta(minutes=config.episode_cooldown_minutes)
        if emitted:
            next_episode_allowed = max(
                next_episode_allowed,
                pd.Timestamp(signals[-1].observed_time_ns, unit="ns", tz="UTC"),
            )

    signals.sort(key=lambda item: item.observed_time_ns)
    seen: set[int] = set()
    unique: list[RotationSignal] = []
    for signal in signals:
        if signal.observed_time_ns in seen:
            continue
        seen.add(signal.observed_time_ns)
        unique.append(signal)
    if any(item.source_max_market_time_ns >= item.observed_time_ns for item in unique):
        raise AssertionError("V156 requires a full completed minute between failure and activation")
    return unique


def state_funnel(state: pd.DataFrame, config: InventoryFailureConfig) -> dict[str, int]:
    context = config.context()
    contexts = informed_inventory_candidate_mask(state, context)
    direction = state["price_direction"]
    midpoint = state["observation_low"] + config.reentry_fraction * (
        state["observation_high"] - state["observation_low"]
    )
    reentry = ((direction > 0) & (state["close"] <= midpoint)) | ((direction < 0) & (state["close"] >= midpoint))
    opposite = state["current_candle_direction"] == -direction
    taker_flip = (-direction * state["taker_one_bar_change"]) > 0.0
    oi_unwind = state["oi_one_bar_change"] < 0.0
    top_unwind = direction * state["top_position_one_bar_change"] < 0.0
    return {
        "observations": int(len(state)),
        "inventory_buildup_contexts": int(contexts.sum()),
        "range_reentry_observations": int(reentry.fillna(False).sum()),
        "opposite_body_observations": int(opposite.fillna(False).sum()),
        "taker_flip_observations": int(taker_flip.fillna(False).sum()),
        "oi_unwind_observations": int(oi_unwind.fillna(False).sum()),
        "top_position_unwind_observations": int(top_unwind.fillna(False).sum()),
    }


def signals_to_json(signals: Sequence[RotationSignal]) -> str:
    return json.dumps([item.to_dict() for item in signals], sort_keys=True, separators=(",", ":"))
