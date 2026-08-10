"""Causal adapter for public ``myshortingstrategiembe2``.

The source is Freqtrade interface v3.  Its legacy ``buy``/``sell`` columns are
not project entries/exits; the effective entry surface is the explicit
``enter_long``/``enter_short`` RSI-cross policy.  Entries are therefore already
rising-edge causal episodes on complete 5-minute candles.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
from typing import Mapping, Sequence

_BASE_PATH = Path(__file__).resolve().with_name("router_picasso.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate57_mbe_reused_primitives", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load reused router primitives: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

BarObservation = _BASE.BarObservation
FeatureObservation = _BASE.FeatureObservation
RouteConfig = _BASE.RouteConfig
RouteDecision = _BASE.RouteDecision
UNRESOLVED = _BASE.UNRESOLVED
_EPS = _BASE._EPS
_aggregate_complete = _BASE._aggregate_complete
_ema = _BASE._ema
_ema_nan = _BASE._ema_nan
_sma = _BASE._sma
_rsi = _BASE._rsi

MBE_STATE = "PUBLIC_MBE2_RSI_TEMA_CROSS"
PICASSO_STATE = MBE_STATE
SMA_OFFSET_STATE = MBE_STATE
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}

_RSI_LOW = 30.0
_RSI_HIGH = 70.0


def _decode_mode(mode: str) -> str:
    normalized = str(mode).strip().lower().replace("-", "_")
    for side in ("both", "long", "short"):
        if normalized.startswith(side + "_") or normalized == side:
            return side
    raise ValueError(f"unsupported Candidate 57 MBE side mode: {mode}")


def _tema(values: Sequence[float], period: int) -> list[float]:
    ema1 = _ema(values, period)
    ema2 = _ema_nan(ema1, period)
    ema3 = _ema_nan(ema2, period)
    out: list[float] = []
    for a, b, c in zip(ema1, ema2, ema3, strict=True):
        if all(math.isfinite(float(value)) for value in (a, b, c)):
            out.append(3.0 * float(a) - 3.0 * float(b) + float(c))
        else:
            out.append(math.nan)
    return out


def mbe_source_flags(
    *,
    previous_rsi: float,
    rsi: float,
    previous_tema: float,
    tema: float,
    bb_middle: float,
    volume: float,
) -> tuple[bool, bool]:
    values = (previous_rsi, rsi, previous_tema, tema, bb_middle, volume)
    if not all(math.isfinite(float(value)) for value in values):
        return False, False
    if volume <= 0.0:
        return False, False
    long_signal = (
        previous_rsi <= _RSI_LOW
        and rsi > _RSI_LOW
        and tema <= bb_middle
        and tema > previous_tema
    )
    short_signal = (
        previous_rsi >= _RSI_HIGH
        and rsi < _RSI_HIGH
        and tema > bb_middle
        and tema < previous_tema
    )
    return bool(long_signal), bool(short_signal)


def _arrays(
    candles: Sequence[BarObservation], config: RouteConfig
) -> dict[str, Sequence[float]]:
    closes = [float(candle.close) for candle in candles]
    tema_period = int(config.picasso_bb_long_period)
    bb_period = int(config.picasso_bb_short_period)
    rsi_period = int(config.picasso_rsi_long_period)
    return {
        "close": closes,
        "volume": [max(0.0, float(candle.volume)) for candle in candles],
        "rsi": _rsi(closes, rsi_period),
        "tema": _tema(closes, tema_period),
        "bb_middle": _sma(closes, bb_period),
        # Diagnostics only.  These never participate in the source entry.
        "ema_2h": _ema(closes, 24),
        "ema_8h": _ema(closes, 96),
    }


def _return_bps(values: Sequence[float], index: int, lookback: int) -> float:
    if index < lookback:
        return math.nan
    previous = float(values[index - lookback])
    current = float(values[index])
    if not (math.isfinite(previous) and math.isfinite(current) and previous > 0.0):
        return math.nan
    return (current / previous - 1.0) * 10_000.0


def _window_std(values: Sequence[float]) -> float:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    if len(clean) < 2:
        return math.nan
    mean = sum(clean) / len(clean)
    return math.sqrt(sum((value - mean) ** 2 for value in clean) / len(clean))


def _signal_at(
    candles: Sequence[BarObservation],
    index: int,
    arrays: Mapping[str, Sequence[float]],
) -> tuple[bool, bool, dict[str, float | int | str]]:
    previous = index - 1
    previous_rsi = float(arrays["rsi"][previous])
    rsi = float(arrays["rsi"][index])
    previous_tema = float(arrays["tema"][previous])
    tema = float(arrays["tema"][index])
    bb_middle = float(arrays["bb_middle"][index])
    volume = float(arrays["volume"][index])
    long_signal, short_signal = mbe_source_flags(
        previous_rsi=previous_rsi,
        rsi=rsi,
        previous_tema=previous_tema,
        tema=tema,
        bb_middle=bb_middle,
        volume=volume,
    )
    closes = arrays["close"]
    volumes = arrays["volume"]
    close = float(closes[index])
    start_1h = max(0, index - 11)
    recent_returns = []
    for cursor in range(max(1, index - 11), index + 1):
        prior = float(closes[cursor - 1])
        current = float(closes[cursor])
        if prior > 0.0 and math.isfinite(prior) and math.isfinite(current):
            recent_returns.append(current / prior - 1.0)
    recent_candles = candles[start_1h : index + 1]
    high_1h = max((float(item.high) for item in recent_candles), default=close)
    low_1h = min((float(item.low) for item in recent_candles), default=close)
    volume_window = [float(value) for value in volumes[max(0, index - 19) : index + 1]]
    mean_volume = sum(volume_window) / len(volume_window) if volume_window else math.nan
    price_window = [float(value) for value in closes[max(0, index - 19) : index + 1]]
    bb_std = _window_std(price_window)
    ema_2h = float(arrays["ema_2h"][index])
    ema_8h = float(arrays["ema_8h"][index])
    return long_signal, short_signal, {
        "close": close,
        "volume": volume,
        "previous_rsi": previous_rsi,
        "rsi": rsi,
        "rsi_cross_magnitude": abs(rsi - previous_rsi),
        "previous_tema": previous_tema,
        "tema": tema,
        "bb_middle": bb_middle,
        "long_cross": int(long_signal),
        "short_cross": int(short_signal),
        "tema_to_middle_bps": (tema - bb_middle) / max(abs(bb_middle), _EPS) * 10_000.0,
        "tema_slope_bps": (tema - previous_tema) / max(abs(close), _EPS) * 10_000.0,
        "bb_width_bps": 4.0 * bb_std / max(abs(bb_middle), _EPS) * 10_000.0,
        "volume_ratio_20": volume / mean_volume if math.isfinite(mean_volume) and mean_volume > 0.0 else math.nan,
        "return_1h_bps": _return_bps(closes, index, 12),
        "return_4h_bps": _return_bps(closes, index, 48),
        "return_8h_bps": _return_bps(closes, index, 96),
        "ema_2h_to_8h_bps": (ema_2h - ema_8h) / max(abs(close), _EPS) * 10_000.0,
        "realized_vol_1h_bps": _window_std(recent_returns) * 10_000.0,
        "range_1h_bps": (high_1h - low_1h) / max(abs(close), _EPS) * 10_000.0,
        "context_diagnostics_only": 1,
    }


def source_signals_for_bars(
    bars: Sequence[BarObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[bool, bool, dict[str, float | int | str]]:
    candles = _aggregate_complete(bars, 5)
    minimum = max(
        int(config.picasso_rsi_long_period) + 2,
        int(config.picasso_bb_long_period) * 3 + 2,
        int(config.picasso_bb_short_period) + 2,
    )
    if len(candles) < minimum:
        return False, False, {
            "reason": "MBE_HISTORY_NOT_READY",
            "candles": len(candles),
            "minimum": minimum,
        }
    arrays = _arrays(candles, config)
    current = len(candles) - 1
    required = (
        arrays["rsi"][current - 1],
        arrays["rsi"][current],
        arrays["tema"][current - 1],
        arrays["tema"][current],
        arrays["bb_middle"][current],
    )
    if not all(math.isfinite(float(value)) for value in required):
        return False, False, {"reason": "MBE_INDICATORS_NOT_READY"}
    return _signal_at(candles, current, arrays)


def _unresolved(
    symbol: str,
    reason: str,
    episode_ts: int = 0,
    diagnostics: Mapping[str, float | int | str] | None = None,
) -> RouteDecision:
    return RouteDecision(
        symbol=symbol,
        state=UNRESOLVED,
        side=0,
        score=0.0,
        entry_reference=math.nan,
        stop_reference=math.nan,
        objective_reference=math.nan,
        episode_ts=int(episode_ts),
        reasons=(reason,),
        diagnostics=dict(diagnostics or {}),
    )


def classify_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    if not bars:
        return _unresolved(symbol, "NO_MINUTE_BARS")
    latest_ts = int(bars[-1].ts_event)
    if int(feature.observed_time_ns) > latest_ts:
        return _unresolved(symbol, "FUTURE_FEATURE_REJECTED", latest_ts)

    long_signal, short_signal, diagnostics = source_signals_for_bars(
        bars, config
    )
    candles = _aggregate_complete(bars, 5)
    current_ts = int(candles[-1].ts_event) if candles else latest_ts
    reason = str(diagnostics.get("reason", ""))
    if reason:
        return _unresolved(symbol, reason, current_ts, diagnostics)

    side_filter = _decode_mode(config.picasso_precedence_mode)
    if side_filter == "long":
        short_signal = False
    elif side_filter == "short":
        long_signal = False
    diagnostics.update(
        {
            "candidate57_declared_mode": str(config.picasso_precedence_mode),
            "source_side_filter": side_filter,
            "long_action": int(long_signal),
            "short_action": int(short_signal),
            "complete_5m_candles_only": 1,
            "source_entry_columns": "enter_long/enter_short",
            "legacy_buy_sell_columns_ignored": 1,
        }
    )
    if long_signal == short_signal:
        return _unresolved(
            symbol,
            "MBE_NO_SOURCE_CROSS" if not long_signal else "MBE_AMBIGUOUS_SOURCE_CROSS",
            current_ts,
            diagnostics,
        )

    side = 1 if long_signal else -1
    entry = float(diagnostics["close"])
    leverage = max(float(config.picasso_source_effective_leverage), _EPS)
    stop_fraction = float(config.picasso_source_stoploss) / leverage
    objective_fraction = float(config.picasso_emergency_target_fraction)
    stop = entry * (1.0 - side * stop_fraction)
    objective = entry * (1.0 + side * objective_fraction)
    rsi = float(diagnostics["rsi"])
    previous_rsi = float(diagnostics["previous_rsi"])
    tema_gap = abs(float(diagnostics["tema_to_middle_bps"]))
    score = 1.0 + min(8.0, abs(rsi - previous_rsi)) + min(4.0, tema_gap / 10.0)
    diagnostics.update(
        {
            "source_tag": "rsi_cross",
            "source_effective_leverage": leverage,
            "source_stoploss_profit_ratio": float(config.picasso_source_stoploss),
            "underlying_stop_fraction": stop_fraction,
            "source_trailing_positive": float(config.picasso_trailing_positive),
            "source_trailing_offset": float(config.picasso_trailing_offset),
        }
    )
    return RouteDecision(
        symbol=symbol,
        state=MBE_STATE,
        side=side,
        score=float(score),
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=objective,
        episode_ts=current_ts,
        reasons=(
            "PUBLIC_MBE2_EXPLICIT_RSI_CROSS_ENTRY",
            "COMPLETE_5M_CANDLE",
            "SOURCE_RISK_NORMALIZED_BY_EFFECTIVE_LEVERAGE",
        ),
        diagnostics=diagnostics,
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
    "MBE_STATE",
    "PICASSO_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "_decode_mode",
    "classify_symbol",
    "mbe_source_flags",
    "route_universe",
    "source_signals_for_bars",
]
