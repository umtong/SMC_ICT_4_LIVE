"""Candidate 51 tournament for high-win public BB_RPB_TSL entry families.

The public monolith is decomposed into independent causal families.  The exact
published observations are retained; execution, one-slot arbitration and risk
are supplied by NautilusTrader.  Short candidates use reciprocal-price symmetry
instead of inventing unrelated thresholds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence

from router_picasso import (
    BarObservation,
    FeatureObservation,
    _aggregate_complete,
    _adx,
    _atr,
    _ema,
    _finite,
    _rsi,
    _rolling_std,
    _sma,
)

BBRPB_STATE_PREFIX = "PUBLIC_BBRPB"
SMA_OFFSET_STATE = BBRPB_STATE_PREFIX
UNRESOLVED = "UNRESOLVED"
_EPS = 1e-12
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}


@dataclass(frozen=True, slots=True)
class RouteConfig:
    atr_period: int = 30
    min_impulse_atr_continuation: float = 0.20
    min_impulse_atr_reversal: float = 1.05
    min_response_atr: float = 0.08
    min_participation_ratio: float = 0.85
    min_route_score: float = 3.20
    ambiguity_score_gap: float = 0.10
    continuation_target_r: float = 2.00
    reversal_target_r: float = 1.65

    bbrpb_family: str = "nfi32"
    bbrpb_allow_short: bool = True
    bbrpb_bucket_minutes: int = 5
    bbrpb_structural_lookback: int = 12
    bbrpb_stop_atr_buffer: float = 0.25
    bbrpb_min_reward_r: float = 0.75
    bbrpb_min_target_fraction: float = 0.004

    sma_offset_period: int = 8
    sma_offset_low: float = 0.960
    sma_offset_high: float = 1.012
    sma_trend_fast: int = 20
    sma_trend_slow: int = 25
    sma_stop_min_fraction: float = 0.0075
    sma_stop_max_fraction: float = 0.1000
    sma_stop_atr_buffer: float = 0.50
    sma_structural_lookback: int = 6
    sma_min_reward_r: float = 1.00


@dataclass(frozen=True, slots=True)
class RouteDecision:
    symbol: str
    state: str
    side: int
    score: float
    entry_reference: float
    stop_reference: float
    objective_reference: float
    episode_ts: int
    reasons: tuple[str, ...] = ()
    diagnostics: Mapping[str, float | int | str] = field(default_factory=dict)

    @property
    def actionable(self) -> bool:
        return self.side in (-1, 1) and self.state != UNRESOLVED


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


def _reciprocal(candles: Sequence[BarObservation]) -> list[BarObservation]:
    result = []
    for candle in candles:
        values = (float(candle.open), float(candle.high), float(candle.low), float(candle.close))
        if not all(_finite(value) and value > 0.0 for value in values):
            return []
        result.append(BarObservation(
            ts_event=int(candle.ts_event),
            open=1.0 / float(candle.open),
            high=1.0 / float(candle.low),
            low=1.0 / float(candle.high),
            close=1.0 / float(candle.close),
            volume=float(candle.volume),
        ))
    return result


def _cti(values: Sequence[float], period: int = 20) -> list[float]:
    result = [math.nan] * len(values)
    if period <= 1:
        return result
    x = [float(index) for index in range(period)]
    mean_x = sum(x) / period
    denom_x = math.sqrt(sum((value - mean_x) ** 2 for value in x))
    for index in range(period - 1, len(values)):
        sample = [float(value) for value in values[index - period + 1:index + 1]]
        mean_y = sum(sample) / period
        denom_y = math.sqrt(sum((value - mean_y) ** 2 for value in sample))
        if denom_x > _EPS and denom_y > _EPS:
            result[index] = sum(
                (x_value - mean_x) * (y_value - mean_y)
                for x_value, y_value in zip(x, sample, strict=True)
            ) / (denom_x * denom_y)
    return result


def _williams(candles: Sequence[BarObservation], period: int = 14) -> list[float]:
    result = [math.nan] * len(candles)
    for index in range(period - 1, len(candles)):
        sample = candles[index - period + 1:index + 1]
        highest = max(float(candle.high) for candle in sample)
        lowest = min(float(candle.low) for candle in sample)
        spread = highest - lowest
        if spread > _EPS:
            result[index] = -100.0 * (highest - float(candles[index].close)) / spread
    return result


def _roc(values: Sequence[float], period: int) -> list[float]:
    result = [math.nan] * len(values)
    for index in range(period, len(values)):
        previous = float(values[index - period])
        if abs(previous) > _EPS:
            result[index] = (float(values[index]) / previous - 1.0) * 100.0
    return result


def _stoch(candles: Sequence[BarObservation], k_period: int = 5, d_period: int = 3) -> tuple[list[float], list[float]]:
    fastk = [math.nan] * len(candles)
    for index in range(k_period - 1, len(candles)):
        sample = candles[index - k_period + 1:index + 1]
        highest = max(float(candle.high) for candle in sample)
        lowest = min(float(candle.low) for candle in sample)
        spread = highest - lowest
        if spread > _EPS:
            fastk[index] = 100.0 * (float(candles[index].close) - lowest) / spread
    fastd = [math.nan] * len(candles)
    for index in range(d_period - 1, len(candles)):
        sample = fastk[index - d_period + 1:index + 1]
        if all(_finite(value) for value in sample):
            fastd[index] = sum(float(value) for value in sample) / d_period
    return fastk, fastd


def _crsi(closes: Sequence[float]) -> list[float]:
    rsi3 = _rsi(closes, 3)
    changes = [0.0]
    for index in range(1, len(closes)):
        changes.append(1.0 if closes[index] > closes[index - 1] else -1.0 if closes[index] < closes[index - 1] else 0.0)
    rsi2 = _rsi(changes, 2)
    roc100 = _roc(closes, 100)
    return [
        (float(a) + float(b) + float(c)) / 3.0
        if all(_finite(value) for value in (a, b, c)) else math.nan
        for a, b, c in zip(rsi3, rsi2, roc100, strict=True)
    ]


def _typical(candles: Sequence[BarObservation]) -> list[float]:
    return [
        (float(candle.high) + float(candle.low) + float(candle.close)) / 3.0
        for candle in candles
    ]


def _arrays(candles: Sequence[BarObservation]) -> dict[str, list[float]]:
    closes = [float(candle.close) for candle in candles]
    opens = [float(candle.open) for candle in candles]
    lows = [float(candle.low) for candle in candles]
    volumes = [max(0.0, float(candle.volume)) for candle in candles]
    typical = _typical(candles)
    ema50 = _ema(closes, 50)
    ema200 = _ema(closes, 200)
    ewo = [
        (float(a) - float(b)) / max(float(low), _EPS) * 100.0
        if _finite(a) and _finite(b) else math.nan
        for a, b, low in zip(ema50, ema200, lows, strict=True)
    ]
    middle = _sma(typical, 20)
    standard = _rolling_std(typical, 20)
    fastk, fastd = _stoch(candles)
    arrays = {
        "close": closes,
        "open": opens,
        "low": lows,
        "volume": volumes,
        "rsi": _rsi(closes, 14),
        "rsi_fast": _rsi(closes, 4),
        "rsi_slow": _rsi(closes, 20),
        "sma15": _sma(closes, 15),
        "ema8": _ema(closes, 8),
        "ema12": _ema(closes, 12),
        "ema13": _ema(closes, 13),
        "ema16": _ema(closes, 16),
        "ema20": _ema(closes, 20),
        "ema26": _ema(closes, 26),
        "ema100": _ema(closes, 100),
        "ema200": ema200,
        "ewo": ewo,
        "cti": _cti(closes, 20),
        "r14": _williams(candles, 14),
        "crsi": _crsi(closes),
        "bbmid": middle,
        "bbwidth": [
            4.0 * float(std) / max(float(mid), _EPS)
            if _finite(std) and _finite(mid) else math.nan
            for mid, std in zip(middle, standard, strict=True)
        ],
        "fastk": fastk,
        "fastd": fastd,
        "adx": _adx(candles, 14),
        "atr": _atr(candles, 14),
    }
    for period in (4, 12, 24):
        mean = _sma(volumes, period)
        arrays[f"volume_mean_{period}"] = [math.nan, *mean[:-1]]
    return arrays


def _aggregate_five_to_hourly(candles: Sequence[BarObservation]) -> list[BarObservation]:
    """Aggregate completed five-minute candles into completed UTC hours."""
    hour_ns = 60 * 60_000_000_000
    grouped: dict[int, list[BarObservation]] = {}
    for candle in candles:
        grouped.setdefault(int(candle.ts_event) // hour_ns, []).append(candle)
    output: list[BarObservation] = []
    for key in sorted(grouped):
        items = sorted(grouped[key], key=lambda item: item.ts_event)
        if len(items) != 12:
            continue
        if any(int(items[index].ts_event) - int(items[index - 1].ts_event) != 5 * 60_000_000_000
               for index in range(1, len(items))):
            continue
        output.append(BarObservation(
            ts_event=int(items[-1].ts_event),
            open=float(items[0].open),
            high=max(float(item.high) for item in items),
            low=min(float(item.low) for item in items),
            close=float(items[-1].close),
            volume=sum(max(0.0, float(item.volume)) for item in items),
        ))
    return output


def _hourly_check(candles: Sequence[BarObservation]) -> tuple[bool, dict[str, float]]:
    hourly = _aggregate_five_to_hourly(candles)
    if len(hourly) < 21:
        return False, {"hourly_candles": float(len(hourly))}
    typical = _typical(hourly)
    middle = _sma(typical, 20)
    standard = _rolling_std(typical, 20)
    roc9 = _roc([float(candle.close) for candle in hourly], 9)
    index = len(hourly) - 1
    if not all(_finite(value) for value in (middle[index], standard[index], roc9[index])):
        return False, {"hourly_candles": float(len(hourly))}
    width = 4.0 * float(standard[index]) / max(float(middle[index]), _EPS)
    ok = float(roc9[index]) < 4.0 and width < 1.074
    return ok, {
        "hourly_candles": float(len(hourly)),
        "roc_1h_9": float(roc9[index]),
        "bb_width_1h": width,
    }


def bbrpb_family_flag(
    family: str,
    index: int,
    arrays: Mapping[str, Sequence[float]],
) -> bool:
    """Return one exact published family condition at ``index``."""
    previous = index - 1
    value = lambda name, at=index: float(arrays[name][at])
    family = str(family).strip().lower()
    if family == "nfi32":
        return (
            value("rsi_slow") < value("rsi_slow", previous)
            and value("rsi_fast") < 46.0
            and value("rsi") > 25.0
            and value("close") < value("sma15") * 0.93
            and value("cti") < -0.90
        )
    if family == "nfi33":
        return (
            value("close") < value("ema13") * 0.978
            and value("ewo") > 8.0
            and value("cti") < -0.88
            and value("rsi") < 32.0
            and value("r14") < -98.0
            and value("volume") < value("volume_mean_4") * 2.5
        )
    if family == "local_dip":
        return (
            value("ema26") > value("ema12")
            and value("ema26") - value("ema12") > value("open") * 0.024
            and value("ema26", previous) - value("ema12", previous) > value("open") / 100.0
            and value("close") < value("ema20") * 1.084
            and value("rsi") < 20.0
            and value("crsi") > 10.0
            and abs(value("close") - value("close", previous)) > value("close") * 13.717 / 1000.0
        )
    if family == "r_deadfish":
        return (
            value("ema100") < value("ema200") * 0.972
            and value("bbwidth") > 0.091
            and value("close") < value("bbmid") * 0.911
            and value("volume_mean_12") > value("volume_mean_24") * 1.008
            and value("cti") < -0.115
            and value("r14") < -44.34
        )
    if family == "cofi":
        return (
            value("open") < value("ema8") * 1.147
            and value("fastk", previous) <= value("fastd", previous)
            and value("fastk") > value("fastd")
            and value("fastk") < 39.0
            and value("fastd") < 28.0
            and value("adx") > 13.0
            and value("ewo") > 8.594
            and value("cti") < -0.892
            and value("r14") < -85.016
        )
    raise ValueError(f"unsupported BB_RPB family: {family}")


def _candidate(
    symbol: str,
    actual: Sequence[BarObservation],
    normalized: Sequence[BarObservation],
    side: int,
    config: RouteConfig,
) -> RouteDecision:
    family = str(config.bbrpb_family).strip().lower()
    arrays = _arrays(normalized)
    index = len(normalized) - 1
    previous = index - 1
    required = ("rsi", "rsi_fast", "rsi_slow", "sma15", "ema8", "ema12", "ema13",
                "ema16", "ema20", "ema26", "ema100", "ema200", "ewo", "cti",
                "r14", "crsi", "bbmid", "bbwidth", "fastk", "fastd", "adx",
                "atr", "volume_mean_4", "volume_mean_12", "volume_mean_24")
    if not all(_finite(arrays[name][index]) for name in required):
        return _unresolved(symbol, "BBRPB_INDICATORS_NOT_READY", normalized[index].ts_event)
    hourly_ok, hourly = _hourly_check(normalized)
    diagnostics: dict[str, float | int | str] = {
        "family": family,
        "side": side,
        **{name: float(arrays[name][index]) for name in (
            "rsi", "rsi_fast", "rsi_slow", "sma15", "ema8", "ema13", "ema16",
            "ema20", "ema100", "ema200", "ewo", "cti", "r14", "crsi", "bbmid",
            "bbwidth", "fastk", "fastd", "adx", "atr")},
        **hourly,
        "additional_check": int(hourly_ok),
    }
    current = hourly_ok and bbrpb_family_flag(family, index, arrays)
    prior = bbrpb_family_flag(family, previous, arrays)
    edge = current and not prior
    diagnostics.update({"family_condition": int(current), "previous_family_condition": int(prior), "rising_edge": int(edge)})
    if not edge:
        return _unresolved(symbol, f"BBRPB_{family.upper()}_NO_EDGE", normalized[index].ts_event, diagnostics)

    entry_n = float(normalized[index].close)
    atr = float(arrays["atr"][index])
    lookback = max(2, int(config.bbrpb_structural_lookback))
    structural = min(float(candle.low) for candle in normalized[-lookback:])
    stop_n = structural - float(config.bbrpb_stop_atr_buffer) * atr
    target_levels = [
        float(arrays[name][index])
        for name in ("ema8", "ema13", "ema16", "sma15", "bbmid")
        if _finite(arrays[name][index]) and float(arrays[name][index]) > entry_n
    ]
    if not target_levels:
        return _unresolved(symbol, "BBRPB_NO_CAUSAL_MEAN_OBJECTIVE", normalized[index].ts_event, diagnostics)
    target_n = max(target_levels)
    if not (stop_n > 0.0 and stop_n < entry_n < target_n):
        return _unresolved(symbol, "BBRPB_INVALID_NORMALIZED_GEOMETRY", normalized[index].ts_event, diagnostics)
    risk = entry_n - stop_n
    reward = target_n - entry_n
    reward_risk = reward / risk
    target_fraction = reward / entry_n
    diagnostics.update({
        "normalized_entry": entry_n,
        "normalized_stop": stop_n,
        "normalized_target": target_n,
        "reward_risk": reward_risk,
        "target_fraction": target_fraction,
    })
    if reward_risk < float(config.bbrpb_min_reward_r):
        return _unresolved(symbol, "BBRPB_REWARD_RISK_TOO_SMALL", normalized[index].ts_event, diagnostics)
    if target_fraction < float(config.bbrpb_min_target_fraction):
        return _unresolved(symbol, "BBRPB_TARGET_SPACE_TOO_SMALL", normalized[index].ts_event, diagnostics)

    actual_entry = float(actual[index].close)
    if side > 0:
        stop, target = stop_n, target_n
    else:
        stop, target = 1.0 / stop_n, 1.0 / target_n
    if side > 0 and not (stop < actual_entry < target):
        return _unresolved(symbol, "BBRPB_LONG_CONVERSION_FAILED", normalized[index].ts_event, diagnostics)
    if side < 0 and not (target < actual_entry < stop):
        return _unresolved(symbol, "BBRPB_SHORT_CONVERSION_FAILED", normalized[index].ts_event, diagnostics)
    score = reward_risk + abs(float(arrays["cti"][index])) + max(0.0, -float(arrays["r14"][index]) / 100.0)
    return RouteDecision(
        symbol=symbol,
        state=f"{BBRPB_STATE_PREFIX}_{family.upper()}",
        side=side,
        score=float(score),
        entry_reference=actual_entry,
        stop_reference=stop,
        objective_reference=target,
        episode_ts=int(normalized[index].ts_event),
        reasons=(
            f"PUBLIC_BBRPB_{family.upper()}_ENTRY",
            "ONE_HOUR_ROC_AND_BOLLINGER_SAFETY",
            "CAUSAL_SWING_STOP_AND_MEAN_REVERSION_OBJECTIVE",
            "RECIPROCAL_SHORT_SYMMETRY" if side < 0 else "SOURCE_LONG_DIRECTION",
        ),
        diagnostics=diagnostics,
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
    candles = _aggregate_complete(bars, int(config.bbrpb_bucket_minutes))
    if len(candles) < 220:
        return _unresolved(symbol, "BBRPB_HISTORY_NOT_READY", latest_ts, {"candles": len(candles)})
    long_decision = _candidate(symbol, candles, candles, 1, config)
    short_decision = _unresolved(symbol, "BBRPB_SHORT_DISABLED", latest_ts)
    if bool(config.bbrpb_allow_short):
        reciprocal = _reciprocal(candles)
        if reciprocal:
            short_decision = _candidate(symbol, candles, reciprocal, -1, config)
    actionable = [item for item in (long_decision, short_decision) if item.actionable]
    if len(actionable) == 1:
        return actionable[0]
    if len(actionable) > 1:
        actionable.sort(key=lambda item: (-item.score, -item.side))
        best = actionable[0]
        if abs(actionable[0].score - actionable[1].score) <= 1e-9:
            return _unresolved(symbol, "BBRPB_LONG_SHORT_AMBIGUITY", best.episode_ts)
        return best
    reasons = tuple(dict.fromkeys((*long_decision.reasons, *short_decision.reasons)))
    return _unresolved(symbol, reasons[0] if reasons else "BBRPB_NO_ENTRY", latest_ts, {
        "long_reason": long_decision.reasons[0] if long_decision.reasons else "",
        "short_reason": short_decision.reasons[0] if short_decision.reasons else "",
    })


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
            features_by_symbol.get(symbol, FeatureObservation(bars[-1].ts_event if bars else 0, ready=True)),
            config,
        )
        for symbol, bars in bars_by_symbol.items()
    }
    actionable = [decision for decision in decisions.values() if decision.actionable]
    actionable.sort(key=lambda item: (-float(item.score), _SYMBOL_PRIORITY.get(item.symbol, 99), int(item.episode_ts)))
    return (actionable[0] if actionable else None), decisions


__all__ = [
    "BBRPB_STATE_PREFIX",
    "BarObservation",
    "FeatureObservation",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "bbrpb_family_flag",
    "classify_symbol",
    "route_universe",
]
