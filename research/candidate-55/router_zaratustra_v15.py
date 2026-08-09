"""Causal structural adapter for public ``ZaratustraV15``.

The public 5-minute futures policy combines two independent entry mechanisms:

* directional state: DX/ADX/+DI/-DI, OBV direction, MFI side and ``ATR < 0.2``;
* Bollinger breakout: close crossing the 20-period typical-price outer band.

The literal ATR threshold is price-scale dependent.  Candidate 55 therefore
keeps the literal source as a diagnostic and declares one dimensionless repair:
``ATR / close < 0.002`` (0.2%).  Project-eligible variants also convert the DI
level into a rising edge so repeated entries inside one causal state cannot
inflate independent trade frequency.  Bollinger conditions are already edges.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
from typing import Mapping, Sequence

_REUSED_PATH = Path(__file__).resolve().with_name("router_zaratustra_v13.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_v15_reused_indicators", _REUSED_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load reused ZaratustraV13 helpers: {_REUSED_PATH}")
_REUSED = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _REUSED
_SPEC.loader.exec_module(_REUSED)

BarObservation = _REUSED.BarObservation
FeatureObservation = _REUSED.FeatureObservation
RouteConfig = _REUSED.RouteConfig
RouteDecision = _REUSED.RouteDecision
UNRESOLVED = _REUSED.UNRESOLVED
_EPS = _REUSED._EPS
_BASE = _REUSED._BASE

ZARATUSTRA_STATE = "PUBLIC_ZARATUSTRA_V15_DI_OR_BB"
PICASSO_STATE = ZARATUSTRA_STATE
SMA_OFFSET_STATE = ZARATUSTRA_STATE
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}

_aggregate_complete = _REUSED._aggregate_complete
_directional_indicators = _REUSED._directional_indicators
_adx_dx = _REUSED._adx_dx
_bollinger = _REUSED._bollinger


def _decode_mode(mode: str) -> tuple[str, str, str]:
    """Return (trigger, atr_policy, component_policy)."""
    normalized = str(mode).strip().lower().replace("-", "_")
    declared = {
        "source_level_exact": ("level", "absolute", "source"),
        "edge_exact": ("edge", "absolute", "source"),
        "edge_normalized": ("edge", "normalized", "source"),
        "edge_normalized_di": ("edge", "normalized", "di"),
        "bb_only": ("edge", "absolute", "bb"),
    }
    if normalized not in declared:
        raise ValueError(f"unsupported Candidate 55 ZaratustraV15 mode: {mode}")
    return declared[normalized]


def _atr(candles: Sequence[BarObservation], period: int) -> list[float]:
    """Wilder ATR with causal SMA seed."""
    size = len(candles)
    output = [math.nan] * size
    if period <= 0 or size <= period:
        return output
    true_range = [0.0] * size
    for index in range(1, size):
        current = candles[index]
        previous_close = float(candles[index - 1].close)
        true_range[index] = max(
            float(current.high) - float(current.low),
            abs(float(current.high) - previous_close),
            abs(float(current.low) - previous_close),
        )
    value = sum(true_range[1 : period + 1]) / period
    output[period] = value
    for index in range(period + 1, size):
        value = ((period - 1.0) * value + true_range[index]) / period
        output[index] = value
    return output


def _obv(candles: Sequence[BarObservation]) -> list[float]:
    """TA-Lib compatible on-balance volume geometry."""
    if not candles:
        return []
    output = [0.0] * len(candles)
    output[0] = float(candles[0].volume)
    for index in range(1, len(candles)):
        close = float(candles[index].close)
        previous = float(candles[index - 1].close)
        volume = float(candles[index].volume)
        if close > previous:
            output[index] = output[index - 1] + volume
        elif close < previous:
            output[index] = output[index - 1] - volume
        else:
            output[index] = output[index - 1]
    return output


def _mfi(candles: Sequence[BarObservation], period: int) -> list[float]:
    """Causal money-flow index using typical-price money flow."""
    size = len(candles)
    output = [math.nan] * size
    if period <= 0 or size <= period:
        return output
    typical = [
        (float(candle.high) + float(candle.low) + float(candle.close)) / 3.0
        for candle in candles
    ]
    raw = [typical[index] * max(0.0, float(candles[index].volume)) for index in range(size)]
    positive = [0.0] * size
    negative = [0.0] * size
    for index in range(1, size):
        if typical[index] > typical[index - 1]:
            positive[index] = raw[index]
        elif typical[index] < typical[index - 1]:
            negative[index] = raw[index]
    pos_sum = sum(positive[1 : period + 1])
    neg_sum = sum(negative[1 : period + 1])
    for index in range(period, size):
        if index > period:
            pos_sum += positive[index] - positive[index - period]
            neg_sum += negative[index] - negative[index - period]
        if neg_sum <= _EPS:
            output[index] = 100.0 if pos_sum > _EPS else 50.0
        else:
            ratio = pos_sum / neg_sum
            output[index] = 100.0 - 100.0 / (1.0 + ratio)
    return output


def _flags_at(
    candles: Sequence[BarObservation],
    index: int,
    *,
    lower: Sequence[float],
    upper: Sequence[float],
    dx: Sequence[float],
    adx: Sequence[float],
    pdi: Sequence[float],
    mdi: Sequence[float],
    atr: Sequence[float],
    obv: Sequence[float],
    mfi: Sequence[float],
    atr_policy: str,
) -> tuple[dict[str, bool], dict[str, float | int | str]]:
    if index <= 0:
        return ({"long_di": False, "long_bb": False, "short_di": False, "short_bb": False}, {})
    current = candles[index]
    previous = candles[index - 1]
    close = float(current.close)
    values = {
        "close": close,
        "previous_close": float(previous.close),
        "lower": float(lower[index]),
        "previous_lower": float(lower[index - 1]),
        "upper": float(upper[index]),
        "previous_upper": float(upper[index - 1]),
        "dx": float(dx[index]),
        "adx": float(adx[index]),
        "pdi": float(pdi[index]),
        "mdi": float(mdi[index]),
        "atr": float(atr[index]),
        "obv": float(obv[index]),
        "previous_obv": float(obv[index - 1]),
        "mfi": float(mfi[index]),
    }
    required = tuple(values.values())
    if not all(math.isfinite(float(value)) for value in required):
        return (
            {"long_di": False, "long_bb": False, "short_di": False, "short_bb": False},
            {**values, "indicators_ready": 0, "atr_policy": atr_policy},
        )
    atr_ratio = float(values["atr"]) / max(close, _EPS)
    atr_ok = (
        float(values["atr"]) < 0.2
        if atr_policy == "absolute"
        else atr_ratio < 0.002
    )
    flags = {
        "long_di": bool(
            float(values["dx"]) > float(values["mdi"])
            and float(values["adx"]) > float(values["mdi"])
            and float(values["pdi"]) > float(values["mdi"])
            and float(values["obv"]) > float(values["previous_obv"])
            and float(values["mfi"]) > 50.0
            and atr_ok
        ),
        "long_bb": bool(
            float(values["previous_close"]) <= float(values["previous_upper"])
            and close > float(values["upper"])
        ),
        "short_di": bool(
            float(values["dx"]) > float(values["pdi"])
            and float(values["adx"]) > float(values["pdi"])
            and float(values["mdi"]) > float(values["pdi"])
            and float(values["obv"]) < float(values["previous_obv"])
            and float(values["mfi"]) < 50.0
            and atr_ok
        ),
        "short_bb": bool(
            float(values["previous_close"]) >= float(values["previous_lower"])
            and close < float(values["lower"])
        ),
    }
    diagnostics: dict[str, float | int | str] = {
        **values,
        "atr_ratio": atr_ratio,
        "atr_policy": atr_policy,
        "atr_ok": int(atr_ok),
        "indicators_ready": 1,
        **{name: int(value) for name, value in flags.items()},
    }
    return flags, diagnostics


def source_entry_flags(
    *,
    dx: float,
    adx: float,
    pdi: float,
    mdi: float,
    obv: float,
    previous_obv: float,
    mfi: float,
    atr: float,
    close: float,
    previous_close: float,
    upper: float,
    previous_upper: float,
    lower: float,
    previous_lower: float,
    normalized_atr: bool = False,
) -> dict[str, bool]:
    """Pure source contract used by CI and provenance checks."""
    atr_ok = atr / max(close, _EPS) < 0.002 if normalized_atr else atr < 0.2
    return {
        "long_di": bool(dx > mdi and adx > mdi and pdi > mdi and obv > previous_obv and mfi > 50.0 and atr_ok),
        "long_bb": bool(previous_close <= previous_upper and close > upper),
        "short_di": bool(dx > pdi and adx > pdi and mdi > pdi and obv < previous_obv and mfi < 50.0 and atr_ok),
        "short_bb": bool(previous_close >= previous_lower and close < lower),
    }


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

    trigger, atr_policy, component = _decode_mode(config.picasso_precedence_mode)
    period = int(config.picasso_adx_period)
    bb_period = int(config.picasso_bb_long_period)
    candles = _aggregate_complete(bars, 5)
    minimum = max(2 * period + 3, bb_period + 3)
    if len(candles) < minimum:
        return _unresolved(
            symbol,
            "ZARATUSTRA_V15_HISTORY_NOT_READY",
            latest_ts,
            {"candles": len(candles), "minimum": minimum},
        )

    pdi, mdi = _directional_indicators(candles, period)
    dx, adx = _adx_dx(pdi, mdi, period)
    lower, middle, upper = _bollinger(candles, bb_period)
    atr = _atr(candles, period)
    obv = _obv(candles)
    mfi = _mfi(candles, period)
    index = len(candles) - 1
    current_flags, diagnostics = _flags_at(
        candles,
        index,
        lower=lower,
        upper=upper,
        dx=dx,
        adx=adx,
        pdi=pdi,
        mdi=mdi,
        atr=atr,
        obv=obv,
        mfi=mfi,
        atr_policy=atr_policy,
    )
    previous_flags, _ = _flags_at(
        candles,
        index - 1,
        lower=lower,
        upper=upper,
        dx=dx,
        adx=adx,
        pdi=pdi,
        mdi=mdi,
        atr=atr,
        obv=obv,
        mfi=mfi,
        atr_policy=atr_policy,
    )

    long_di = current_flags["long_di"]
    short_di = current_flags["short_di"]
    if trigger == "edge":
        long_di = long_di and not previous_flags["long_di"]
        short_di = short_di and not previous_flags["short_di"]
    if component == "di":
        long_action = long_di
        short_action = short_di
    elif component == "bb":
        long_action = current_flags["long_bb"]
        short_action = current_flags["short_bb"]
    else:
        long_action = long_di or current_flags["long_bb"]
        short_action = short_di or current_flags["short_bb"]

    diagnostics.update(
        {
            "candidate55_declared_mode": str(config.picasso_precedence_mode),
            "source_trigger_mode": trigger,
            "source_atr_policy": atr_policy,
            "source_component_policy": component,
            "previous_long_di": int(previous_flags["long_di"]),
            "previous_short_di": int(previous_flags["short_di"]),
            "effective_long_di": int(long_di),
            "effective_short_di": int(short_di),
            "long_action": int(long_action),
            "short_action": int(short_action),
            "bb_middle": float(middle[index]),
            "complete_5m_candles_only": 1,
            "project_independent_episode_semantics": int(trigger == "edge"),
            "literal_absolute_atr_preserved": int(atr_policy == "absolute"),
            "dimensionless_atr_repair": int(atr_policy == "normalized"),
        }
    )
    if long_action == short_action:
        return _unresolved(
            symbol,
            "ZARATUSTRA_V15_NO_ACTION" if not long_action else "ZARATUSTRA_V15_DUAL_SIDE_CONFLICT",
            int(candles[index].ts_event),
            diagnostics,
        )

    side = 1 if long_action else -1
    entry = float(candles[index].close)
    leverage = max(float(config.picasso_source_effective_leverage), _EPS)
    stop_fraction = float(config.picasso_source_stoploss) / leverage
    stop = entry * (1.0 - side * stop_fraction)
    objective = entry * (1.0 + side * float(config.picasso_emergency_target_fraction))

    used_di = bool(long_di if side > 0 else short_di)
    used_bb = bool(current_flags["long_bb"] if side > 0 else current_flags["short_bb"])
    directional_margin = (
        min(
            float(dx[index]) - float(mdi[index]),
            float(adx[index]) - float(mdi[index]),
            float(pdi[index]) - float(mdi[index]),
        )
        if side > 0
        else min(
            float(dx[index]) - float(pdi[index]),
            float(adx[index]) - float(pdi[index]),
            float(mdi[index]) - float(pdi[index]),
        )
    )
    breakout_bps = (
        (entry - float(upper[index])) / entry * 10_000.0
        if side > 0
        else (float(lower[index]) - entry) / entry * 10_000.0
    )
    mfi_margin = abs(float(mfi[index]) - 50.0)
    score = (
        1.0
        + (2.5 if used_di else 0.0)
        + (2.5 if used_bb else 0.0)
        + min(4.0, max(0.0, directional_margin) / 3.0)
        + min(3.0, max(0.0, breakout_bps) / 12.0)
        + min(2.0, mfi_margin / 15.0)
    )
    diagnostics.update(
        {
            "source_tag": (
                "Long DI+BB enter" if side > 0 and used_di and used_bb
                else "Long DI enter" if side > 0 and used_di
                else "Long Bollinger enter" if side > 0
                else "Short DI+BB enter" if used_di and used_bb
                else "Short DI enter" if used_di
                else "Short Bollinger enter"
            ),
            "used_di_component": int(used_di),
            "used_bb_component": int(used_bb),
            "directional_margin": directional_margin,
            "breakout_bps": breakout_bps,
            "mfi_margin": mfi_margin,
            "source_effective_leverage": leverage,
            "source_stoploss_profit_ratio": float(config.picasso_source_stoploss),
            "underlying_stop_fraction": stop_fraction,
            "source_trailing_positive": float(config.picasso_trailing_positive),
            "source_trailing_offset": float(config.picasso_trailing_offset),
        }
    )
    return RouteDecision(
        symbol=symbol,
        state=ZARATUSTRA_STATE,
        side=side,
        score=float(score),
        entry_reference=entry,
        stop_reference=stop,
        objective_reference=objective,
        episode_ts=int(candles[index].ts_event),
        reasons=(
            "PUBLIC_ZARATUSTRA_V15_ENTRY",
            "SOURCE_TRIGGER_" + trigger.upper(),
            "ATR_POLICY_" + atr_policy.upper(),
            "COMPONENT_POLICY_" + component.upper(),
            "SOURCE_RISK_NORMALIZED_BY_10X_LEVERAGE",
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
    "PICASSO_STATE",
    "RouteConfig",
    "RouteDecision",
    "SMA_OFFSET_STATE",
    "UNRESOLVED",
    "ZARATUSTRA_STATE",
    "_adx_dx",
    "_aggregate_complete",
    "_atr",
    "_bollinger",
    "_decode_mode",
    "_directional_indicators",
    "_mfi",
    "_obv",
    "classify_symbol",
    "route_universe",
    "source_entry_flags",
]
