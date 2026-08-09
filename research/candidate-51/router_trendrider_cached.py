"""Cached completed-bar facade for the public TrendRider policy.

This module preserves ``router_trendrider`` semantics but accepts already
completed 1h/4h/1d candles from the strategy adapter.  It avoids repeatedly
scanning tens of thousands of minute bars at every hourly decision.

Source-parity details are explicit: the traded symbol uses the tuned RSI16,
BTC context uses the source's RSI14, and an available-but-not-yet-warmed daily
series leaves EMA200 unavailable rather than replacing it with zero.
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence

import router_trendrider as _base

BarObservation = _base.BarObservation
EntryEvaluation = _base.EntryEvaluation
FeatureObservation = _base.FeatureObservation
RouteConfig = _base.RouteConfig
RouteDecision = _base.RouteDecision
SMA_OFFSET_STATE = _base.SMA_OFFSET_STATE
TRENDRIDER_STATE = _base.TRENDRIDER_STATE
TrendSnapshot = _base.TrendSnapshot
UNRESOLVED = _base.UNRESOLVED
classify_symbol = _base.classify_symbol
trendrider_exit_signal = _base.trendrider_exit_signal


def evaluate_entry_aggregated(
    symbol: str,
    hours: Sequence[BarObservation],
    four_hours: Sequence[BarObservation],
    days: Sequence[BarObservation],
    btc_hours: Sequence[BarObservation],
    config: RouteConfig = RouteConfig(),
) -> EntryEvaluation:
    snapshot = _base._snapshot(hours, config)
    if snapshot is None or len(hours) < 206:
        return EntryEvaluation(
            None,
            0,
            "UNRESOLVED",
            (),
            snapshot,
            {},
            "TRENDRIDER_HISTORY_NOT_READY",
        )
    previous = _base._snapshot(hours, config, -2)
    if previous is None:
        return EntryEvaluation(
            None,
            0,
            "UNRESOLVED",
            (),
            snapshot,
            {},
            "TRENDRIDER_PREVIOUS_STATE_NOT_READY",
        )

    four_snapshot = (
        _base._snapshot(four_hours, config)
        if len(four_hours) >= 205
        else None
    )
    is_bull_4h = int(four_snapshot.is_bull) if four_snapshot else 0
    adx_4h = float(four_snapshot.adx) if four_snapshot else 0.0

    # The public Freqtrade dataframe contains the daily column whenever daily
    # data exists.  Before 200 completed days its EMA is NaN, so the
    # trend_pullback branch must remain false.  Zero is used only for the
    # source's true no-data fallback.
    daily_ema_200 = math.nan if days else 0.0
    if len(days) >= 200:
        values = _base._ema([float(candle.close) for candle in days], 200)
        if values and _base._finite(values[-1]):
            daily_ema_200 = float(values[-1])

    # BTC context in the public source is fixed RSI14 + EMA50/EMA200,
    # independent of the traded symbol's tuned RSI16.
    btc_rsi = 50.0
    btc_is_bull = 1
    if len(btc_hours) >= 201:
        btc_closes = [float(candle.close) for candle in btc_hours]
        btc_rsi_values = _base._rsi(btc_closes, 14)
        btc_ema_50 = _base._ema(btc_closes, 50)
        btc_ema_200 = _base._ema(btc_closes, 200)
        if _base._finite(
            btc_rsi_values[-1],
            btc_ema_50[-1],
            btc_ema_200[-1],
        ):
            btc_rsi = float(btc_rsi_values[-1])
            btc_is_bull = int(
                btc_closes[-1] > float(btc_ema_200[-1])
                and float(btc_ema_50[-1]) > float(btc_ema_200[-1])
            )

    context: dict[str, float | int | str] = {
        "is_bull_4h": is_bull_4h,
        "adx_4h": adx_4h,
        "daily_ema_200": daily_ema_200,
        "btc_rsi_1h": btc_rsi,
        "btc_is_bull_1h": btc_is_bull,
        "private_fng_value": 50.0,
        "private_funding_rate": 0.0,
    }

    tags: list[str] = []
    btc_ok = btc_rsi > 35.0
    volume_positive = snapshot.volume > 0.0

    pullback_to_ema = (
        snapshot.low <= snapshot.ema_slow * 1.02
        and snapshot.close > snapshot.ema_slow
        and snapshot.close > snapshot.open
    )
    if (
        snapshot.is_bull
        and pullback_to_ema
        and snapshot.rsi > float(config.trendrider_rsi_pullback_low)
        and snapshot.rsi < float(config.trendrider_rsi_pullback_high)
        and snapshot.adx > float(config.trendrider_adx_threshold)
        and snapshot.volume_ratio > float(config.trendrider_volume_factor)
        and snapshot.plus_di > snapshot.minus_di
        and snapshot.obv > snapshot.obv_ema
        and volume_positive
        and btc_ok
        and snapshot.rsi < 70.0
        and math.isfinite(daily_ema_200)
        and snapshot.close > daily_ema_200
    ):
        tags.append("trend_pullback")

    ema50_bounce = (
        snapshot.low <= snapshot.ema_50 * 1.01
        and snapshot.close > snapshot.ema_50
        and snapshot.close > snapshot.open
    )
    if (
        snapshot.is_bull
        and ema50_bounce
        and 30.0 < snapshot.rsi < 50.0
        and snapshot.adx > 20.0
        and snapshot.volume_ratio > 1.0
        and snapshot.macd_hist > snapshot.macd_hist_prev
        and volume_positive
        and btc_ok
        and snapshot.rsi < 70.0
    ):
        tags.append("ema50_bounce")

    if (
        previous.rsi < float(config.trendrider_rsi_bounce)
        and snapshot.rsi > float(config.trendrider_rsi_bounce)
        and snapshot.close > snapshot.ema_200
        and snapshot.close > snapshot.bb_lower
        and snapshot.close > snapshot.open
        and snapshot.volume_ratio > 0.8
        and snapshot.obv > snapshot.obv_ema
        and volume_positive
        and btc_ok
    ):
        tags.append("rsi_bounce")

    if (
        snapshot.ema_fast > snapshot.ema_slow
        and previous.ema_fast <= previous.ema_slow
        and 40.0 < snapshot.rsi < 75.0
        and snapshot.close > snapshot.ema_200
        and snapshot.volume_ratio > 0.5
        and volume_positive
        and btc_ok
    ):
        tags.append("ema_crossover")

    if (
        snapshot.close <= snapshot.bb_lower * 1.005
        and snapshot.close > snapshot.open
        and snapshot.rsi < 45.0
        and snapshot.volume_ratio > 0.7
        and snapshot.adx > 18.0
        and volume_positive
        and btc_ok
    ):
        tags.append("bb_bounce")

    if (
        snapshot.macd_hist > 0.0
        and previous.macd_hist <= 0.0
        and snapshot.close > snapshot.ema_50
        and snapshot.close > snapshot.ema_200
        and 40.0 < snapshot.rsi < 60.0
        and snapshot.adx > 15.0
        and snapshot.volume_ratio > 0.8
        and volume_positive
        and btc_ok
    ):
        tags.append("macd_reversal")

    confidence, details = _base._confidence(snapshot, context, config)
    regime = _base._regime(snapshot)
    if not tags:
        return EntryEvaluation(
            None,
            confidence,
            regime,
            details,
            snapshot,
            context,
            "TRENDRIDER_NO_PUBLIC_ENTRY_BRANCH",
        )
    minimum = (
        int(config.trendrider_min_confidence_bear)
        if "BEAR" in regime
        else int(config.trendrider_min_confidence_normal)
    )
    selected = max(tags, key=lambda tag: _base._TAG_PRIORITY[tag])
    if confidence < minimum:
        return EntryEvaluation(
            selected,
            confidence,
            regime,
            details,
            snapshot,
            context,
            "TRENDRIDER_CONFIDENCE_REJECTED",
        )
    return EntryEvaluation(
        selected,
        confidence,
        regime,
        details,
        snapshot,
        context,
        "",
    )


def route_universe_aggregated(
    hours_by_symbol: Mapping[str, Sequence[BarObservation]],
    four_hours_by_symbol: Mapping[str, Sequence[BarObservation]],
    days_by_symbol: Mapping[str, Sequence[BarObservation]],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    btc_hours = hours_by_symbol.get("BTCUSDT", ())
    decisions: dict[str, RouteDecision] = {}
    for symbol, hours in hours_by_symbol.items():
        evaluation = evaluate_entry_aggregated(
            symbol,
            hours,
            four_hours_by_symbol.get(symbol, ()),
            days_by_symbol.get(symbol, ()),
            btc_hours,
            config,
        )
        snapshot = evaluation.snapshot
        episode_ts = int(snapshot.ts_event) if snapshot else 0
        diagnostics: dict[str, float | int | str] = {
            "entry_tag": evaluation.tag or "",
            "confidence": int(evaluation.confidence),
            "regime": evaluation.regime,
            "confidence_details": "|".join(evaluation.details),
            "reject_reason": evaluation.reject_reason,
            **dict(evaluation.context),
            "evaluation_path": "cached-completed-1h-4h-1d",
            "btc_context_rsi_period": 14,
            "traded_symbol_rsi_period": int(config.trendrider_rsi_period),
        }
        if snapshot is not None:
            diagnostics.update(
                {
                    "rsi": snapshot.rsi,
                    "adx": snapshot.adx,
                    "plus_di": snapshot.plus_di,
                    "minus_di": snapshot.minus_di,
                    "volume_ratio": snapshot.volume_ratio,
                    "macd_hist": snapshot.macd_hist,
                    "ema_fast": snapshot.ema_fast,
                    "ema_slow": snapshot.ema_slow,
                    "ema_50": snapshot.ema_50,
                    "ema_200": snapshot.ema_200,
                    "bb_lower": snapshot.bb_lower,
                    "bb_upper": snapshot.bb_upper,
                }
            )
        if evaluation.reject_reason:
            decisions[symbol] = _base._unresolved(
                symbol,
                evaluation.reject_reason,
                episode_ts,
                diagnostics,
            )
            continue
        assert snapshot is not None and evaluation.tag is not None
        entry = snapshot.close
        stop = entry * (1.0 - float(config.trendrider_stop_fraction))
        objective = entry * (
            1.0 + float(config.trendrider_remote_target_fraction)
        )
        if not (0.0 < stop < entry < objective):
            decisions[symbol] = _base._unresolved(
                symbol,
                "TRENDRIDER_GEOMETRY_INVALID",
                episode_ts,
                diagnostics,
            )
            continue
        diagnostics["reward_r"] = (objective - entry) / (entry - stop)
        decisions[symbol] = RouteDecision(
            symbol=symbol,
            state=TRENDRIDER_STATE,
            side=1,
            score=float(evaluation.confidence)
            + 0.01 * float(_base._TAG_PRIORITY[evaluation.tag]),
            entry_reference=entry,
            stop_reference=stop,
            objective_reference=objective,
            episode_ts=episode_ts,
            reasons=(
                f"PUBLIC_ENTRY_TAG_{evaluation.tag.upper()}",
                "PUBLIC_REGIME_CONFIDENCE_GATE",
                "PUBLIC_SIX_PERCENT_HARD_INVALIDATION",
                "PUBLIC_ROI_TRAILING_AND_CASCADE_MANAGEMENT",
            ),
            diagnostics=diagnostics,
        )
    actionable = [
        decision for decision in decisions.values() if decision.actionable
    ]
    actionable.sort(
        key=lambda decision: (
            -decision.score,
            _base._SYMBOL_PRIORITY.get(decision.symbol, 99),
            decision.episode_ts,
        )
    )
    return (actionable[0] if actionable else None), decisions


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    return _base.route_universe(bars_by_symbol, features_by_symbol, config)


__all__ = [
    "BarObservation",
    "EntryEvaluation",
    "FeatureObservation",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "TRENDRIDER_STATE",
    "TrendSnapshot",
    "UNRESOLVED",
    "classify_symbol",
    "evaluate_entry_aggregated",
    "route_universe",
    "route_universe_aggregated",
    "trendrider_exit_signal",
]
