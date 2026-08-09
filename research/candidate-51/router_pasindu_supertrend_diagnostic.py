"""Diagnostic facade for the live-effective Pasindu Supertrend policy.

The economic rules are identical to ``router_pasindu_supertrend``.  This facade
only separates source-stage refusal reasons so a zero-trade account can be
classified as implementation/warmup failure, no flip opportunity, disabled
regime, confidence rejection, or invalid geometry instead of one opaque no
signal result.
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence

import router_pasindu_supertrend as _base

BarObservation = _base.BarObservation
FeatureObservation = _base.FeatureObservation
IndicatorState = _base.IndicatorState
PASINDU_CONTINUATION_STATE = _base.PASINDU_CONTINUATION_STATE
PASINDU_FLIP_STATE = _base.PASINDU_FLIP_STATE
RouteConfig = _base.RouteConfig
RouteDecision = _base.RouteDecision
SMA_OFFSET_STATE = _base.SMA_OFFSET_STATE
SourceSignal = _base.SourceSignal
UNRESOLVED = _base.UNRESOLVED
_supertrend = _base._supertrend
_recent_flip = _base._recent_flip
_confidence = _base._confidence
_regime_scores = _base._regime_scores
_indicator_state = _base._indicator_state
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}


def evaluate_source(
    hours: Sequence[BarObservation],
    four_hours: Sequence[BarObservation],
    config: RouteConfig,
) -> tuple[SourceSignal | None, str, dict[str, float | int | str]]:
    diagnostics: dict[str, float | int | str] = {
        "source_policy": str(config.pasindu_mode),
        "hour_bars": len(hours),
        "four_hour_bars": len(four_hours),
        "live_supertrend_period": int(config.pasindu_supertrend_period),
        "live_supertrend_multiplier": float(config.pasindu_supertrend_multiplier),
    }
    if len(hours) < 105 or len(four_hours) < 105:
        return None, "PASINDU_HISTORY_NOT_READY", diagnostics
    state4 = _base._indicator_state(four_hours, config)
    if state4 is None:
        return None, "PASINDU_INDICATORS_NOT_READY", diagnostics
    diagnostics.update(
        {
            "regime": state4.regime,
            "regime_confidence": state4.regime_confidence,
            "adx_4h": state4.adx,
            "atr_4h": state4.atr,
            "ema9_4h": state4.ema9,
            "ema21_4h": state4.ema21,
            "rsi_4h": state4.rsi,
            "supertrend_direction_4h": state4.direction,
            "bb_width_ratio": state4.bb_width_ratio,
            "atr_ratio": state4.atr_ratio,
            "volume_ratio": state4.volume_ratio,
            "hurst": state4.hurst,
        }
    )
    if state4.adx < float(config.pasindu_adx_min):
        return None, "PASINDU_ADX_BELOW_LIVE_GATE", diagnostics
    if state4.regime not in {"trending", "ranging"}:
        return None, "PASINDU_REDUCED_MODE_REGIME_DISABLED", diagnostics
    if state4.regime == "ranging" and state4.adx < 18.0:
        return None, "PASINDU_RANGING_DEAD_ZONE", diagnostics

    _, directions4 = _base._supertrend(
        four_hours,
        int(config.pasindu_supertrend_period),
        float(config.pasindu_supertrend_multiplier),
    )
    valid4 = [int(value) for value in directions4 if math.isfinite(value)]
    if len(valid4) < 2:
        return None, "PASINDU_4H_DIRECTION_NOT_READY", diagnostics
    current4, previous4 = valid4[-1], valid4[-2]
    diagnostics.update(
        {
            "previous_direction_4h": previous4,
            "current_direction_4h": current4,
            "direct_flip": int(previous4 != current4),
        }
    )
    side = 0
    kind = ""
    episode_ts = int(four_hours[-1].ts_event)
    flip_age = 1
    if previous4 != current4:
        side = current4
        kind = "4h_flip"
    elif str(config.pasindu_mode).strip().lower() == "reduced_live":
        established = int(config.pasindu_established_4h_bars)
        if len(valid4) < established or len(set(valid4[-established:])) != 1:
            return None, "PASINDU_4H_DIRECTION_NOT_ESTABLISHED", diagnostics
        _, directions1 = _base._supertrend(
            hours,
            int(config.pasindu_supertrend_period),
            float(config.pasindu_supertrend_multiplier),
        )
        valid1 = [value for value in directions1 if math.isfinite(value)]
        if not valid1:
            return None, "PASINDU_1H_DIRECTION_NOT_READY", diagnostics
        diagnostics["current_direction_1h"] = int(valid1[-1])
        if int(valid1[-1]) != current4:
            return None, "PASINDU_1H_NOT_ALIGNED", diagnostics
        recent = _base._recent_flip(
            directions1,
            current4,
            int(config.pasindu_continuation_lookback_1h),
        )
        if recent is None:
            return None, "PASINDU_NO_RECENT_1H_FLIP", diagnostics
        flip_age, source_index = recent
        side = current4
        kind = "1h_continuation"
        episode_ts = int(hours[source_index].ts_event)
    elif str(config.pasindu_mode).strip().lower() == "flip_only":
        return None, "PASINDU_NO_4H_FLIP", diagnostics
    else:
        return None, "PASINDU_UNKNOWN_SOURCE_POLICY", diagnostics

    continuation = kind == "1h_continuation"
    confidence = _base._confidence(state4, side, continuation)
    if continuation:
        confidence *= max(0.6, 1.0 - (flip_age - 1) * 0.1)
    diagnostics.update(
        {
            "signal_kind": kind,
            "side": side,
            "flip_age": flip_age,
            "confidence": confidence,
        }
    )
    if confidence < float(config.pasindu_confidence_min):
        return None, "PASINDU_CONFIDENCE_BELOW_LIVE_GATE", diagnostics

    entry = float(hours[-1].close)
    sl_mult, tp_mult = (
        (2.5, 5.0) if state4.regime == "ranging" else (3.0, 6.0)
    )
    stop = entry - side * state4.atr * sl_mult
    target = entry + side * state4.atr * tp_mult
    valid = (
        0.0 < stop < entry < target
        if side > 0
        else 0.0 < target < entry < stop
    )
    diagnostics.update(
        {
            "entry": entry,
            "stop": stop,
            "target": target,
            "sl_atr_mult": sl_mult,
            "tp_atr_mult": tp_mult,
            "reward_r": (
                abs(target - entry) / abs(entry - stop)
                if abs(entry - stop) > 1e-12
                else math.nan
            ),
        }
    )
    if not valid:
        return None, "PASINDU_SOURCE_GEOMETRY_INVALID", diagnostics
    signal = SourceSignal(
        state=(
            PASINDU_CONTINUATION_STATE
            if continuation
            else PASINDU_FLIP_STATE
        ),
        side=side,
        confidence=confidence,
        entry=entry,
        stop=stop,
        target=target,
        episode_ts=episode_ts,
        regime=state4.regime,
        atr=state4.atr,
        signal_kind=kind,
        flip_age=flip_age,
        diagnostics=diagnostics,
    )
    return signal, "", diagnostics


def route_universe_aggregated(
    hours_by_symbol: Mapping[str, Sequence[BarObservation]],
    four_hours_by_symbol: Mapping[str, Sequence[BarObservation]],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    decisions: dict[str, RouteDecision] = {}
    for symbol, hours in hours_by_symbol.items():
        signal, reason, diagnostics = evaluate_source(
            hours,
            four_hours_by_symbol.get(symbol, ()),
            config,
        )
        ts = int(hours[-1].ts_event) if hours else 0
        if signal is None:
            decisions[symbol] = RouteDecision(
                symbol=symbol,
                state=UNRESOLVED,
                side=0,
                score=0.0,
                entry_reference=math.nan,
                stop_reference=math.nan,
                objective_reference=math.nan,
                episode_ts=ts,
                reasons=(reason,),
                diagnostics=diagnostics,
            )
            continue
        decisions[symbol] = RouteDecision(
            symbol=symbol,
            state=signal.state,
            side=signal.side,
            score=float(signal.confidence),
            entry_reference=signal.entry,
            stop_reference=signal.stop,
            objective_reference=signal.target,
            episode_ts=signal.episode_ts,
            reasons=(
                "LIVE_EFFECTIVE_SUPERTREND8X2",
                f"SOURCE_{signal.signal_kind.upper()}",
                "SOURCE_REDUCED_LIVE_REGIME_ROUTER",
                "SOURCE_CONFIDENCE_GATE_45",
                "SOURCE_REGIME_SPECIFIC_TWO_R_BRACKET",
                "SOURCE_REVERSAL_AND_ATR_TRAIL_MANAGEMENT",
            ),
            diagnostics=diagnostics,
        )
    actionable = [
        decision for decision in decisions.values() if decision.actionable
    ]
    actionable.sort(
        key=lambda item: (
            -item.score,
            _SYMBOL_PRIORITY.get(item.symbol, 99),
            item.episode_ts,
        )
    )
    return (actionable[0] if actionable else None), decisions


def classify_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    del feature
    hours = _base._aggregate_complete(bars, 60)
    four_hours = _base._aggregate_complete(bars, 240)
    _, decisions = route_universe_aggregated(
        {symbol: hours},
        {symbol: four_hours},
        config,
    )
    return decisions[symbol]


classify_sma_offset = classify_symbol


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    del features_by_symbol
    return route_universe_aggregated(
        {
            symbol: _base._aggregate_complete(bars, 60)
            for symbol, bars in bars_by_symbol.items()
        },
        {
            symbol: _base._aggregate_complete(bars, 240)
            for symbol, bars in bars_by_symbol.items()
        },
        config,
    )


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "IndicatorState",
    "PASINDU_CONTINUATION_STATE",
    "PASINDU_FLIP_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "SourceSignal",
    "UNRESOLVED",
    "classify_symbol",
    "evaluate_source",
    "route_universe",
    "route_universe_aggregated",
    "_confidence",
    "_recent_flip",
    "_regime_scores",
    "_supertrend",
]
