"""Candidate 55 adapter for the public 1h RSI/BB/MACD/ADX futures bot.

The reusable Candidate 51 implementation correctly preserved the public
strategy's Python operator-precedence bug, but converted the source's level
entry condition into a rising-edge event.  Freqtrade evaluates the entry level
on every completed source candle and may re-enter after a prior trade closes
while the condition remains true.  Candidate 55 therefore makes the two
semantics explicit and testable:

* precedence: ``exact`` (bug-compatible) or ``corrected`` (intended grouping),
* trigger: ``level`` (source-compatible re-entry) or ``edge`` (deduplicated),
* side: ``all``, ``short`` or ``long``.

Examples are ``exact_level``, ``exact_level_short`` and ``corrected_edge``.
All indicators, source risk geometry, causal candle aggregation and the
NautilusTrader execution shell are reused from Candidate 51.  Only the missing
source-entry semantics are supplied here.
"""
from __future__ import annotations

from dataclasses import replace
import importlib.util
import math
from pathlib import Path
import sys
from typing import Mapping, Sequence

_BASE_PATH = Path(__file__).resolve().with_name("router_picasso.py")
_SPEC = importlib.util.spec_from_file_location("candidate55_picasso_reused_base", _BASE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load reused Picasso router: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

BarObservation = _BASE.BarObservation
FeatureObservation = _BASE.FeatureObservation
RouteConfig = _BASE.RouteConfig
RouteDecision = _BASE.RouteDecision
PICASSO_STATE = _BASE.PICASSO_STATE
SMA_OFFSET_STATE = PICASSO_STATE
UNRESOLVED = _BASE.UNRESOLVED
_EPS = _BASE._EPS
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}

# Reused helpers are intentionally exported because strategy_picasso imports
# these names from the materialized ``router`` module.
_aggregate_complete = _BASE._aggregate_complete
_atr = _BASE._atr
_ema = _BASE._ema


def _decode_mode(mode: str) -> tuple[str, str, str]:
    """Return (precedence, trigger, side_filter) from a declared variant."""
    normalized = str(mode).strip().lower().replace("-", "_")
    if normalized.startswith("directional_relaxed"):
        precedence = "directional_relaxed"
    elif normalized.startswith("corrected"):
        precedence = "corrected"
    elif normalized.startswith("exact"):
        precedence = "exact"
    else:
        raise ValueError(f"unsupported Candidate 55 Picasso mode: {mode}")

    trigger = "level" if "_level" in normalized else "edge"
    if normalized.endswith("_short"):
        side_filter = "short"
    elif normalized.endswith("_long"):
        side_filter = "long"
    else:
        side_filter = "all"
    return precedence, trigger, side_filter


def picasso_source_flags(*, mode: str, adx: float, trend_long: bool,
                         trend_short: bool, volume: float,
                         volume_mean_long: float, volume_mean_short: float,
                         pump_warning: bool,
                         config: RouteConfig = RouteConfig()) -> tuple[bool, bool]:
    """Expose source precedence while accepting Candidate 55 variant names."""
    precedence, _, side_filter = _decode_mode(mode)
    source_config = replace(config, picasso_precedence_mode=precedence)
    long_ok, short_ok = _BASE.picasso_source_flags(
        mode=precedence,
        adx=adx,
        trend_long=trend_long,
        trend_short=trend_short,
        volume=volume,
        volume_mean_long=volume_mean_long,
        volume_mean_short=volume_mean_short,
        pump_warning=pump_warning,
        config=source_config,
    )
    if side_filter == "short":
        long_ok = False
    elif side_filter == "long":
        short_ok = False
    return bool(long_ok), bool(short_ok)


def _unresolved(symbol: str, reason: str, episode_ts: int = 0,
                diagnostics: Mapping[str, float | int | str] | None = None) -> RouteDecision:
    return RouteDecision(
        symbol,
        UNRESOLVED,
        0,
        0.0,
        math.nan,
        math.nan,
        math.nan,
        int(episode_ts),
        (reason,),
        dict(diagnostics or {}),
    )


def classify_symbol(symbol: str, bars: Sequence[BarObservation],
                    feature: FeatureObservation,
                    config: RouteConfig = RouteConfig()) -> RouteDecision:
    """Classify the latest complete source candle without future information."""
    if not bars:
        return _unresolved(symbol, "NO_MINUTE_BARS")
    latest_ts = int(bars[-1].ts_event)
    if int(feature.observed_time_ns) > latest_ts:
        return _unresolved(symbol, "FUTURE_FEATURE_REJECTED", latest_ts)

    precedence, trigger, side_filter = _decode_mode(config.picasso_precedence_mode)
    source_config = replace(config, picasso_precedence_mode=precedence)
    candles = _BASE._aggregate_complete(bars, int(source_config.picasso_bucket_minutes))
    minimum = max(
        60,
        int(source_config.picasso_volume_long_period) + 25,
        int(source_config.picasso_volume_short_period) + 25,
        int(source_config.picasso_adx_period) * 2 + 5,
        35,
    )
    if len(candles) < minimum:
        return _unresolved(
            symbol,
            "PICASSO_HISTORY_NOT_READY",
            latest_ts,
            {"candles": len(candles), "minimum": minimum},
        )

    closes = [float(candle.close) for candle in candles]
    volumes = [max(0.0, float(candle.volume)) for candle in candles]
    arrays: dict[str, Sequence[float]] = {
        "rsi_l": _BASE._rsi(closes, int(source_config.picasso_rsi_long_period)),
        "rsi_s": _BASE._rsi(closes, int(source_config.picasso_rsi_short_period)),
        "mid_l": _BASE._sma(closes, int(source_config.picasso_bb_long_period)),
        "std_l": _BASE._rolling_std(closes, int(source_config.picasso_bb_long_period)),
        "mid_s": _BASE._sma(closes, int(source_config.picasso_bb_short_period)),
        "std_s": _BASE._rolling_std(closes, int(source_config.picasso_bb_short_period)),
        "adx": _BASE._adx(candles, int(source_config.picasso_adx_period)),
        "volume_mean_l": _BASE._rolling_mean_shifted(
            volumes, int(source_config.picasso_volume_long_period)
        ),
        "volume_mean_s": _BASE._rolling_mean_shifted(
            volumes, int(source_config.picasso_volume_short_period)
        ),
    }
    macd, signal = _BASE._macd(closes)
    arrays["macd"], arrays["signal"] = macd, signal

    pump_reference = [math.nan] * len(volumes)
    for index in range(len(volumes)):
        if index >= 24:
            sample = volumes[index - 24:index - 19]
            if len(sample) == 5:
                pump_reference[index] = sum(sample) / 5.0
    arrays["pump_reference"] = pump_reference

    index, previous = len(candles) - 1, len(candles) - 2
    required_values = [
        arrays[name][index]
        for name in (
            "rsi_l",
            "rsi_s",
            "mid_l",
            "std_l",
            "mid_s",
            "std_s",
            "adx",
            "volume_mean_l",
            "volume_mean_s",
            "macd",
            "signal",
        )
    ]
    if not all(_BASE._finite(value) for value in required_values):
        return _unresolved(
            symbol, "PICASSO_INDICATORS_NOT_READY", int(candles[index].ts_event)
        )

    long_level, short_level, diagnostics = _BASE._signal_at(
        candles, index, source_config, arrays
    )
    previous_long, previous_short, _ = _BASE._signal_at(
        candles, previous, source_config, arrays
    )
    if side_filter == "short":
        long_level = False
        previous_long = False
    elif side_filter == "long":
        short_level = False
        previous_short = False

    long_edge = bool(long_level and not previous_long)
    short_edge = bool(short_level and not previous_short)
    if trigger == "level":
        long_action, short_action = bool(long_level), bool(short_level)
    else:
        long_action, short_action = long_edge, short_edge

    diagnostics.update(
        {
            "candidate55_declared_mode": str(config.picasso_precedence_mode),
            "source_precedence_mode": precedence,
            "source_trigger_mode": trigger,
            "source_side_filter": side_filter,
            "previous_long_condition": int(previous_long),
            "previous_short_condition": int(previous_short),
            "long_level": int(long_level),
            "short_level": int(short_level),
            "long_rising_edge": int(long_edge),
            "short_rising_edge": int(short_edge),
            "long_action": int(long_action),
            "short_action": int(short_action),
        }
    )
    if long_action == short_action:
        reason = (
            "PICASSO_NO_SOURCE_LEVEL"
            if trigger == "level" and not long_action
            else "PICASSO_NO_SOURCE_EDGE"
            if not long_action
            else "PICASSO_AMBIGUOUS_SOURCE_SIGNAL"
        )
        return _unresolved(symbol, reason, int(candles[index].ts_event), diagnostics)

    side = 1 if long_action else -1
    entry = float(candles[index].close)
    leverage = max(float(source_config.picasso_source_effective_leverage), _EPS)
    stop_fraction = float(source_config.picasso_source_stoploss) / leverage
    target_fraction = float(source_config.picasso_emergency_target_fraction)
    stop = entry * (1.0 - side * stop_fraction)
    objective = entry * (1.0 + side * target_fraction)

    directional = int(
        diagnostics["trend_long"] if side > 0 else diagnostics["trend_short"]
    )
    volume_mean = float(
        diagnostics["volume_mean_long"]
        if side > 0
        else diagnostics["volume_mean_short"]
    )
    volume_ratio = float(diagnostics["volume"]) / max(volume_mean, _EPS)
    macd_gap = (
        abs(float(diagnostics["macd"]) - float(diagnostics["macd_signal"]))
        / entry
        * 10_000.0
    )
    score = (
        1.0
        + 2.0 * directional
        + min(3.0, max(0.0, volume_ratio - 1.0))
        + min(3.0, macd_gap)
    )
    diagnostics.update(
        {
            "source_tag": "buy_1" if side > 0 else "buy_2",
            "source_effective_leverage": leverage,
            "source_stoploss_profit_ratio": float(
                source_config.picasso_source_stoploss
            ),
            "underlying_stop_fraction": stop_fraction,
            "source_trailing_positive": float(
                source_config.picasso_trailing_positive
            ),
            "source_trailing_offset": float(source_config.picasso_trailing_offset),
            "source_precedence_preserved": int(precedence == "exact"),
            "source_level_reentry_preserved": int(trigger == "level"),
        }
    )
    return RouteDecision(
        symbol,
        PICASSO_STATE,
        side,
        float(score),
        entry,
        stop,
        objective,
        int(candles[index].ts_event),
        (
            "PUBLIC_RSI_BB_MACD_1H_ENTRY",
            "SOURCE_PRECEDENCE_MODE_" + precedence.upper(),
            "SOURCE_TRIGGER_MODE_" + trigger.upper(),
            "SOURCE_SIDE_FILTER_" + side_filter.upper(),
            "SOURCE_RISK_NORMALIZED_BY_EFFECTIVE_LEVERAGE",
        ),
        diagnostics,
    )


classify_sma_offset = classify_symbol


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    decisions = {
        symbol: classify_symbol(
            symbol,
            bars,
            features_by_symbol.get(
                symbol,
                FeatureObservation(bars[-1].ts_event if bars else 0, ready=True),
            ),
            config,
        )
        for symbol, bars in bars_by_symbol.items()
    }
    actionable = [decision for decision in decisions.values() if decision.actionable]
    actionable.sort(
        key=lambda item: (
            -float(item.score),
            _SYMBOL_PRIORITY.get(item.symbol, 99),
            int(item.episode_ts),
        )
    )
    return (actionable[0] if actionable else None), decisions


__all__ = [
    "BarObservation",
    "FeatureObservation",
    "PICASSO_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "_aggregate_complete",
    "_atr",
    "_ema",
    "classify_symbol",
    "picasso_source_flags",
    "route_universe",
]
