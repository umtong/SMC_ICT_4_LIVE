"""Single-variable v91 ablation: remove the confirmation minute.

The original relative-value event definitions, adaptive thresholds, leader/
laggard separation, observable pre-event-basis target, invalidation geometry,
cost model and risk are unchanged.  The signal becomes available at the event
minute close rather than waiting one additional completed minute.  This tests
whether the original confirmation consumed the tradable fair-value gap.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from v53_nt_core import CostConfig, RotationSignal, cost_after_reward_risk
from v91_cross_market_fair_value_core import (
    CrossMarketFairValueConfig,
    build_state,
)

UTC = "UTC"
NS_MINUTE = 60_000_000_000


def _finite(row: pd.Series, names: tuple[str, ...]) -> bool:
    return all(math.isfinite(float(row[name])) for name in names)


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: CrossMarketFairValueConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = pd.Timestamp(evaluation_start)
    end = pd.Timestamp(evaluation_end)
    start = start.tz_localize(UTC) if start.tzinfo is None else start.tz_convert(UTC)
    end = end.tz_localize(UTC) if end.tzinfo is None else end.tz_convert(UTC)
    if end <= start:
        raise ValueError("evaluation end must be after start")

    raw_view = raw[["open", "high", "low", "close"]].rename(
        columns={"open": "raw_open", "high": "raw_high", "low": "raw_low", "close": "raw_close"}
    )
    x = state.join(raw_view, how="inner")
    if x.index.has_duplicates:
        raise ValueError("duplicate v91 ablation timestamps")

    required = (
        "perp_event_return", "spot_event_return",
        "perp_event_flow_ratio", "spot_event_flow_ratio",
        "perp_event_total_quote", "spot_event_total_quote",
        "perp_abs_return_threshold", "spot_abs_return_threshold",
        "perp_turnover_threshold", "spot_turnover_threshold",
        "pre_event_basis", "perp_spot_log_basis", "atr",
        "spot_close", "raw_close",
    )
    signals: list[RotationSignal] = []
    cooldown_until = -1
    index = x.index
    window = config.event_window_minutes

    for position in range(window, len(x)):
        event_ts = index[position]
        if event_ts < start or event_ts >= end:
            continue
        observed_ns = int(event_ts.value)
        if observed_ns <= cooldown_until:
            continue
        event = x.iloc[position]
        if not _finite(event, required):
            continue

        spot_return = float(event["spot_event_return"])
        perp_return = float(event["perp_event_return"])
        spot_abs = abs(spot_return)
        perp_abs = abs(perp_return)
        basis_change = float(event["event_basis_change"])
        pre_basis = float(event["pre_event_basis"])
        atr = float(event["atr"])
        if atr <= 0.0:
            continue

        side = None
        state_name = None
        event_direction = 0
        gap_share = math.nan
        event_flow_alignment = math.nan
        score = math.nan

        spot_direction = int(np.sign(spot_return))
        if spot_direction != 0 and spot_abs >= float(event["spot_abs_return_threshold"]):
            spot_flow_alignment = spot_direction * float(event["spot_event_flow_ratio"])
            perp_participation = spot_direction * perp_return / max(spot_abs, 1e-12)
            lag_gap = -spot_direction * basis_change
            lag_share = lag_gap / max(spot_abs, 1e-12)
            if (
                config.mode in {"SPOT_LED_CATCHUP", "STATE_PORTFOLIO"}
                and spot_flow_alignment >= config.minimum_event_flow_alignment
                and float(event["spot_event_total_quote"]) >= float(event["spot_turnover_threshold"])
                and perp_participation <= config.maximum_spot_led_perp_participation
                and lag_share >= config.minimum_spot_led_lag_share
            ):
                side = "BUY" if spot_direction > 0 else "SELL"
                state_name = "SPOT_LED_CATCHUP"
                event_direction = spot_direction
                gap_share = lag_share
                event_flow_alignment = spot_flow_alignment
                score = lag_gap * max(
                    float(event["spot_event_total_quote"]) / max(float(event["spot_turnover_threshold"]), 1e-12),
                    1.0,
                )

        if side is None:
            perp_direction = int(np.sign(perp_return))
            if perp_direction != 0 and perp_abs >= float(event["perp_abs_return_threshold"]):
                perp_flow_alignment = perp_direction * float(event["perp_event_flow_ratio"])
                spot_participation = perp_direction * spot_return / max(perp_abs, 1e-12)
                overshoot_gap = perp_direction * basis_change
                overshoot_share = overshoot_gap / max(perp_abs, 1e-12)
                if (
                    config.mode in {"PERP_OVERSHOOT_REVERSION", "STATE_PORTFOLIO"}
                    and perp_flow_alignment >= config.minimum_event_flow_alignment
                    and float(event["perp_event_total_quote"]) >= float(event["perp_turnover_threshold"])
                    and spot_participation <= config.maximum_overshoot_spot_participation
                    and overshoot_share >= config.minimum_overshoot_basis_share
                ):
                    side = "SELL" if perp_direction > 0 else "BUY"
                    state_name = "PERP_OVERSHOOT_REVERSION"
                    event_direction = perp_direction
                    gap_share = overshoot_share
                    event_flow_alignment = perp_flow_alignment
                    score = overshoot_gap * max(
                        float(event["perp_event_total_quote"]) / max(float(event["perp_turnover_threshold"]), 1e-12),
                        1.0,
                    )

        if side is None or state_name is None:
            continue

        entry = float(event["raw_close"])
        fair_value = float(event["spot_close"]) * math.exp(pre_basis)
        target = entry + config.target_fair_value_fraction * (fair_value - entry)
        formation = raw.loc[index[position - window + 1] : event_ts]
        if len(formation) < window:
            continue
        if side == "BUY":
            stop = float(formation["low"].min()) - config.stop_buffer_atr * atr
            geometry = stop < entry < target
        else:
            stop = float(formation["high"].max()) + config.stop_buffer_atr * atr
            geometry = target < entry < stop
        if not geometry:
            continue
        rr = cost_after_reward_risk(entry=entry, stop=stop, target=target, side=side, costs=costs)
        if not math.isfinite(rr) or not config.minimum_cost_after_rr <= rr <= config.maximum_cost_after_rr:
            continue

        details = {
            "state": state_name,
            "ablation": "remove_confirmation_minute",
            "event_window_minutes": window,
            "event_close_utc": event_ts.isoformat(),
            "event_direction": event_direction,
            "spot_event_return": spot_return,
            "perp_event_return": perp_return,
            "event_basis_change": basis_change,
            "pre_event_basis": pre_basis,
            "event_flow_alignment": event_flow_alignment,
            "gap_share": gap_share,
            "spot_implied_fair_perp": fair_value,
            "target_fair_value_fraction": config.target_fair_value_fraction,
            "entry_order_type": "MARKET",
            "confirmation": "none; event close is the first causal tradable instant",
        }
        signals.append(
            RotationSignal(
                scenario_id=f"v91-ablate-confirmation-{state_name.lower()}-{window}m-{observed_ns}",
                observed_time_ns=observed_ns,
                side=side,
                entry_reference=entry,
                stop_price=stop,
                target_price=target,
                cost_after_reward_risk=rr,
                score=float(score),
                max_hold_minutes=config.maximum_holding_minutes,
                source_feature_open_time_ns=int(index[position - window].value),
                source_feature_available_time_ns=observed_ns,
                source_max_market_time_ns=observed_ns,
                details=details,
            )
        )
        cooldown_until = observed_ns + config.cooldown_minutes * NS_MINUTE

    for signal in signals:
        if signal.source_max_market_time_ns > signal.observed_time_ns:
            raise AssertionError("future information detected in v91 confirmation ablation")
    return signals


__all__ = [
    "CrossMarketFairValueConfig",
    "build_state",
    "build_rotation_signals",
]
