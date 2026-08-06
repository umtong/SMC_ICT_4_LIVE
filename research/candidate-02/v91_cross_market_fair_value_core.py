"""Cross-market fair-value catch-up for candidate-02 v91.

The strategy does not forecast the direction of a common BTC shock.  It trades
an observable relative-value error between Binance spot and USD-M perpetuals:

* SPOT_LED_CATCHUP: spot discovers a price first, the perpetual underreacts,
  and the next completed minute starts closing the basis gap.
* PERP_OVERSHOOT_REVERSION: the perpetual moves beyond spot-implied fair value,
  and the next completed minute starts closing the excess basis.

All thresholds use only prior completed minutes.  The signal becomes available
only after the confirmation minute closes.  NautilusTrader owns all orders,
fills, positions, commissions and account NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from v53_nt_core import CostConfig, RotationSignal, cost_after_reward_risk

UTC = "UTC"
NS_MINUTE = 60_000_000_000
MODES = {"SPOT_LED_CATCHUP", "PERP_OVERSHOOT_REVERSION", "STATE_PORTFOLIO"}


@dataclass(frozen=True, slots=True)
class CrossMarketFairValueConfig:
    mode: str = "STATE_PORTFOLIO"
    event_window_minutes: int = 2
    prior_window_minutes: int = 2880
    prior_minimum_minutes: int = 720
    spot_return_quantile: float = 0.65
    perp_return_quantile: float = 0.65
    spot_turnover_quantile: float = 0.50
    perp_turnover_quantile: float = 0.50
    minimum_event_flow_alignment: float = 0.15
    maximum_spot_led_perp_participation: float = 0.65
    minimum_spot_led_lag_share: float = 0.25
    minimum_spot_retention: float = 0.50
    minimum_catchup_basis_closure_share: float = 0.10
    minimum_catchup_confirmation_flow: float = 0.0
    maximum_overshoot_spot_participation: float = 0.45
    minimum_overshoot_basis_share: float = 0.30
    minimum_overshoot_basis_closure_share: float = 0.10
    maximum_overshoot_confirmation_flow: float = 0.0
    maximum_overshoot_spot_retention: float = 0.65
    target_fair_value_fraction: float = 1.0
    atr_lookback_minutes: int = 60
    stop_buffer_atr: float = 0.10
    cooldown_minutes: int = 5
    maximum_holding_minutes: int = 60
    minimum_cost_after_rr: float = 0.80
    maximum_cost_after_rr: float = 5.0

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "CrossMarketFairValueConfig":
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"unknown v91 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown v91 mode: {self.mode}")
        if self.event_window_minutes not in {1, 2, 3}:
            raise ValueError("event_window_minutes must be 1, 2 or 3")
        if self.prior_window_minutes < 1440 or self.prior_minimum_minutes < 360:
            raise ValueError("insufficient prior completed-minute history")
        for name in (
            "spot_return_quantile", "perp_return_quantile",
            "spot_turnover_quantile", "perp_turnover_quantile",
        ):
            value = float(getattr(self, name))
            if not 0.0 < value < 1.0:
                raise ValueError(f"invalid {name}")
        if not 0.0 < self.minimum_event_flow_alignment < 1.0:
            raise ValueError("invalid event flow floor")
        if not -0.5 <= self.maximum_spot_led_perp_participation < 1.0:
            raise ValueError("invalid spot-led perpetual participation ceiling")
        if not 0.0 < self.minimum_spot_led_lag_share <= 2.0:
            raise ValueError("invalid lag-share floor")
        if not 0.0 <= self.minimum_spot_retention <= 2.0:
            raise ValueError("invalid spot-retention floor")
        if not 0.0 <= self.minimum_catchup_basis_closure_share <= 1.0:
            raise ValueError("invalid catch-up closure floor")
        if not -1.0 < self.minimum_catchup_confirmation_flow < 1.0:
            raise ValueError("invalid catch-up confirmation flow")
        if not -0.5 <= self.maximum_overshoot_spot_participation < 1.0:
            raise ValueError("invalid overshoot spot-participation ceiling")
        if not 0.0 < self.minimum_overshoot_basis_share <= 2.0:
            raise ValueError("invalid overshoot basis-share floor")
        if not 0.0 <= self.minimum_overshoot_basis_closure_share <= 1.0:
            raise ValueError("invalid overshoot closure floor")
        if not -1.0 < self.maximum_overshoot_confirmation_flow < 1.0:
            raise ValueError("invalid overshoot confirmation flow")
        if not 0.0 <= self.maximum_overshoot_spot_retention <= 2.0:
            raise ValueError("invalid overshoot spot-retention ceiling")
        if not 0.5 <= self.target_fair_value_fraction <= 1.25:
            raise ValueError("target fair-value fraction outside structural range")
        if self.atr_lookback_minutes < 30 or self.maximum_holding_minutes <= 0:
            raise ValueError("invalid risk horizon")
        if self.stop_buffer_atr < 0.0 or self.cooldown_minutes < 0:
            raise ValueError("invalid stop or cooldown")
        if not 0.0 < self.minimum_cost_after_rr <= self.maximum_cost_after_rr:
            raise ValueError("invalid cost-after reward/risk band")


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    result = numerator / denominator.replace(0.0, np.nan)
    return result.replace([np.inf, -np.inf], np.nan)


def build_state(features: pd.DataFrame, config: CrossMarketFairValueConfig) -> pd.DataFrame:
    required = {
        "open", "high", "low", "close",
        "aggressive_total_quote_1m", "aggressive_signed_quote_1m",
        "spot_open", "spot_high", "spot_low", "spot_close",
        "spot_aggressive_total_quote_1m", "spot_aggressive_signed_quote_1m",
        "perp_spot_log_basis",
    }
    missing = sorted(required - set(features.columns))
    if missing:
        raise ValueError(f"v91 missing required completed-minute features: {missing}")

    x = features.copy().sort_index()
    if x.index.tz is None:
        x.index = x.index.tz_localize(UTC)
    else:
        x.index = x.index.tz_convert(UTC)
    if x.index.has_duplicates:
        raise ValueError("duplicate v91 feature timestamps")

    window = config.event_window_minutes
    x["perp_event_return"] = np.log(x["close"] / x["close"].shift(window))
    x["spot_event_return"] = np.log(x["spot_close"] / x["spot_close"].shift(window))
    x["perp_event_total_quote"] = x["aggressive_total_quote_1m"].rolling(window, min_periods=window).sum()
    x["perp_event_signed_quote"] = x["aggressive_signed_quote_1m"].rolling(window, min_periods=window).sum()
    x["spot_event_total_quote"] = x["spot_aggressive_total_quote_1m"].rolling(window, min_periods=window).sum()
    x["spot_event_signed_quote"] = x["spot_aggressive_signed_quote_1m"].rolling(window, min_periods=window).sum()
    x["perp_event_flow_ratio"] = _safe_ratio(x["perp_event_signed_quote"], x["perp_event_total_quote"])
    x["spot_event_flow_ratio"] = _safe_ratio(x["spot_event_signed_quote"], x["spot_event_total_quote"])
    x["pre_event_basis"] = x["perp_spot_log_basis"].shift(window)
    x["event_basis_change"] = x["perp_spot_log_basis"] - x["pre_event_basis"]

    prior = config.prior_window_minutes
    minimum = config.prior_minimum_minutes
    x["spot_abs_return_threshold"] = (
        x["spot_event_return"].abs().rolling(prior, min_periods=minimum).quantile(config.spot_return_quantile).shift(1)
    )
    x["perp_abs_return_threshold"] = (
        x["perp_event_return"].abs().rolling(prior, min_periods=minimum).quantile(config.perp_return_quantile).shift(1)
    )
    x["spot_turnover_threshold"] = (
        x["spot_event_total_quote"].rolling(prior, min_periods=minimum).quantile(config.spot_turnover_quantile).shift(1)
    )
    x["perp_turnover_threshold"] = (
        x["perp_event_total_quote"].rolling(prior, min_periods=minimum).quantile(config.perp_turnover_quantile).shift(1)
    )

    previous_close = x["close"].shift(1)
    true_range = pd.concat(
        [
            x["high"] - x["low"],
            (x["high"] - previous_close).abs(),
            (x["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    x["atr"] = true_range.rolling(
        config.atr_lookback_minutes,
        min_periods=max(20, config.atr_lookback_minutes // 2),
    ).mean().shift(1)
    return x


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
        raise ValueError("duplicate v91 joined timestamps")

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

    for position in range(window, len(x) - 1):
        event_ts = index[position]
        confirmation_ts = index[position + 1]
        if confirmation_ts < start or confirmation_ts >= end:
            continue
        observed_ns = int(confirmation_ts.value)
        if observed_ns <= cooldown_until:
            continue
        event = x.iloc[position]
        confirmation = x.iloc[position + 1]
        if not _finite(event, required):
            continue
        if not _finite(
            confirmation,
            ("spot_close", "raw_close", "perp_spot_log_basis", "signed_flow_ratio_1m"),
        ):
            continue

        spot_return = float(event["spot_event_return"])
        perp_return = float(event["perp_event_return"])
        spot_abs = abs(spot_return)
        perp_abs = abs(perp_return)
        basis_change = float(event["event_basis_change"])
        event_basis = float(event["perp_spot_log_basis"])
        pre_basis = float(event["pre_event_basis"])
        atr = float(event["atr"])
        if atr <= 0.0:
            continue

        side = None
        state_name = None
        event_direction = 0
        gap_share = math.nan
        confirmation_basis_closure_share = math.nan
        retention = math.nan
        event_flow_alignment = math.nan
        score = math.nan

        # Spot-led information first reaches the cash market.  The perpetual is
        # tradable only after it starts closing the lagging basis gap.
        spot_direction = int(np.sign(spot_return))
        if spot_direction != 0 and spot_abs >= float(event["spot_abs_return_threshold"]):
            spot_flow_alignment = spot_direction * float(event["spot_event_flow_ratio"])
            perp_participation = spot_direction * perp_return / max(spot_abs, 1e-12)
            lag_gap = -spot_direction * basis_change
            lag_share = lag_gap / max(spot_abs, 1e-12)
            confirmation_basis_move = spot_direction * (
                float(confirmation["perp_spot_log_basis"]) - event_basis
            )
            confirmation_basis_closure_share = confirmation_basis_move / max(lag_gap, 1e-12)
            spot_total_move = spot_direction * math.log(
                float(confirmation["spot_close"]) / float(x.iloc[position - window]["spot_close"])
            )
            retention = spot_total_move / max(spot_abs, 1e-12)
            confirmation_perp_move = spot_direction * math.log(
                float(confirmation["raw_close"]) / float(event["raw_close"])
            )
            confirmation_flow = spot_direction * float(confirmation["signed_flow_ratio_1m"])
            if (
                config.mode in {"SPOT_LED_CATCHUP", "STATE_PORTFOLIO"}
                and spot_flow_alignment >= config.minimum_event_flow_alignment
                and float(event["spot_event_total_quote"]) >= float(event["spot_turnover_threshold"])
                and perp_participation <= config.maximum_spot_led_perp_participation
                and lag_share >= config.minimum_spot_led_lag_share
                and retention >= config.minimum_spot_retention
                and confirmation_perp_move > 0.0
                and confirmation_basis_closure_share >= config.minimum_catchup_basis_closure_share
                and confirmation_flow >= config.minimum_catchup_confirmation_flow
            ):
                side = "BUY" if spot_direction > 0 else "SELL"
                state_name = "SPOT_LED_CATCHUP"
                event_direction = spot_direction
                event_flow_alignment = spot_flow_alignment
                score = lag_gap * max(
                    float(event["spot_event_total_quote"]) / max(float(event["spot_turnover_threshold"]), 1e-12),
                    1.0,
                )

        # A derivative-only price shock is a relative-value overshoot only once
        # basis convergence starts and aggressive perpetual flow no longer
        # supports the original direction.
        if side is None:
            perp_direction = int(np.sign(perp_return))
            if perp_direction != 0 and perp_abs >= float(event["perp_abs_return_threshold"]):
                perp_flow_alignment = perp_direction * float(event["perp_event_flow_ratio"])
                spot_participation = perp_direction * spot_return / max(perp_abs, 1e-12)
                overshoot_gap = perp_direction * basis_change
                overshoot_share = overshoot_gap / max(perp_abs, 1e-12)
                confirmation_basis_close = -perp_direction * (
                    float(confirmation["perp_spot_log_basis"]) - event_basis
                )
                confirmation_basis_closure_share = confirmation_basis_close / max(overshoot_gap, 1e-12)
                spot_total_move = perp_direction * math.log(
                    float(confirmation["spot_close"]) / float(x.iloc[position - window]["spot_close"])
                )
                retention = spot_total_move / max(perp_abs, 1e-12)
                confirmation_flow = perp_direction * float(confirmation["signed_flow_ratio_1m"])
                if (
                    config.mode in {"PERP_OVERSHOOT_REVERSION", "STATE_PORTFOLIO"}
                    and perp_flow_alignment >= config.minimum_event_flow_alignment
                    and float(event["perp_event_total_quote"]) >= float(event["perp_turnover_threshold"])
                    and spot_participation <= config.maximum_overshoot_spot_participation
                    and overshoot_share >= config.minimum_overshoot_basis_share
                    and retention <= config.maximum_overshoot_spot_retention
                    and confirmation_basis_closure_share >= config.minimum_overshoot_basis_closure_share
                    and confirmation_flow <= config.maximum_overshoot_confirmation_flow
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

        entry = float(confirmation["raw_close"])
        fair_value = float(confirmation["spot_close"]) * math.exp(pre_basis)
        target = entry + config.target_fair_value_fraction * (fair_value - entry)
        formation = raw.loc[index[position - window + 1] : confirmation_ts]
        if len(formation) < window + 1:
            continue
        if side == "BUY":
            stop = float(formation["low"].min()) - config.stop_buffer_atr * atr
            geometry = stop < entry < target
        else:
            stop = float(formation["high"].max()) + config.stop_buffer_atr * atr
            geometry = target < entry < stop
        if not geometry:
            continue
        rr = cost_after_reward_risk(
            entry=entry,
            stop=stop,
            target=target,
            side=side,
            costs=costs,
        )
        if not math.isfinite(rr) or not config.minimum_cost_after_rr <= rr <= config.maximum_cost_after_rr:
            continue

        details = {
            "state": state_name,
            "event_window_minutes": window,
            "event_close_utc": event_ts.isoformat(),
            "confirmation_close_utc": confirmation_ts.isoformat(),
            "event_direction": event_direction,
            "spot_event_return": spot_return,
            "perp_event_return": perp_return,
            "event_basis_change": basis_change,
            "pre_event_basis": pre_basis,
            "event_basis": event_basis,
            "event_flow_alignment": event_flow_alignment,
            "gap_share": gap_share if math.isfinite(gap_share) else (
                -event_direction * basis_change / max(spot_abs, 1e-12)
            ),
            "confirmation_basis_closure_share": confirmation_basis_closure_share,
            "spot_retention_or_participation": retention,
            "spot_implied_fair_perp": fair_value,
            "target_fair_value_fraction": config.target_fair_value_fraction,
            "entry_order_type": "MARKET",
            "causal_interpretation": (
                "spot-led perpetual catch-up" if state_name == "SPOT_LED_CATCHUP"
                else "perpetual overshoot reverting toward pre-event spot-implied basis"
            ),
        }
        signals.append(
            RotationSignal(
                scenario_id=f"v91-{state_name.lower()}-{window}m-{observed_ns}",
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
            raise AssertionError("future information detected in v91")
    return signals


__all__ = [
    "CrossMarketFairValueConfig",
    "build_state",
    "build_rotation_signals",
]
