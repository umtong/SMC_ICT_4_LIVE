"""Causal four-symbol adaptation of the public TrendRider v2.11 long policy.

External performance claims are not copied: the public repository is long-only
while the website displays private short signals and additional private data
layers.  This module implements only the auditable public OHLCV policy:

* six explicit one-hour long entry branches;
* the public tuned values, including values outside the declared hyperopt range;
* the public confidence gate and regime-dependent minimum;
* a frozen six-percent hard invalidation and remote 22.9-percent source ROI;
* completed four-hour, daily, and BTC context with no future filling.

Source management (ROI ladder, trailing, indicator exits, and cascading time
cuts) is exposed through ``trendrider_exit_signal`` and completed by the
Nautilus strategy adapter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Sequence

from router_picasso import (
    BarObservation,
    _adx,
    _aggregate_complete,
    _ema,
    _macd,
    _rma,
    _rolling_std,
    _rsi,
    _sma,
)

TRENDRIDER_STATE = "PUBLIC_TRENDRIDER_V211_LONG"
SMA_OFFSET_STATE = TRENDRIDER_STATE
UNRESOLVED = "UNRESOLVED"
_EPS = 1e-12
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}
_TAG_PRIORITY = {
    "trend_pullback": 0,
    "ema50_bounce": 1,
    "rsi_bounce": 2,
    "ema_crossover": 3,
    "bb_bounce": 4,
    "macd_reversal": 5,
}


@dataclass(frozen=True, slots=True)
class FeatureObservation:
    observed_time_ns: int
    ready: bool = True


@dataclass(frozen=True, slots=True)
class RouteConfig:
    # Fields required by the reused execution shell.
    atr_period: int = 30
    min_impulse_atr_continuation: float = 0.20
    min_impulse_atr_reversal: float = 1.05
    min_response_atr: float = 0.08
    min_participation_ratio: float = 0.85
    min_route_score: float = 3.20
    ambiguity_score_gap: float = 0.10
    continuation_target_r: float = 2.00
    reversal_target_r: float = 1.65

    trendrider_bucket_minutes: int = 60
    trendrider_ema_fast: int = 9
    trendrider_ema_slow: int = 16
    trendrider_rsi_period: int = 16
    trendrider_rsi_pullback_low: float = 30.0
    trendrider_rsi_pullback_high: float = 65.0
    trendrider_rsi_bounce: float = 35.0
    trendrider_adx_threshold: float = 18.0
    trendrider_volume_factor: float = 0.70
    trendrider_rsi_exit: float = 78.0
    trendrider_min_confidence_normal: int = 5
    trendrider_min_confidence_bear: int = 6
    trendrider_stop_fraction: float = 0.06
    trendrider_remote_target_fraction: float = 0.229

    # Legacy compatibility fields ignored by this policy.
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


@dataclass(frozen=True, slots=True)
class TrendSnapshot:
    ts_event: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    ema_fast: float
    ema_slow: float
    ema_50: float
    ema_200: float
    rsi: float
    adx: float
    plus_di: float
    minus_di: float
    macd_hist: float
    macd_hist_prev: float
    bb_upper: float
    bb_middle: float
    bb_lower: float
    bb_width: float
    bb_width_sma: float
    volume_ratio: float
    obv: float
    obv_ema: float
    is_bull: bool
    is_bear: bool


@dataclass(frozen=True, slots=True)
class EntryEvaluation:
    tag: str | None
    confidence: int
    regime: str
    details: tuple[str, ...]
    snapshot: TrendSnapshot | None
    context: Mapping[str, float | int | str]
    reject_reason: str


def _finite(*values: float) -> bool:
    return all(math.isfinite(float(value)) for value in values)


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


def _di(candles: Sequence[BarObservation], period: int) -> tuple[list[float], list[float]]:
    size = len(candles)
    plus_result = [math.nan] * size
    minus_result = [math.nan] * size
    if period <= 0 or size <= period:
        return plus_result, minus_result
    tr = [0.0] * size
    plus_dm = [0.0] * size
    minus_dm = [0.0] * size
    for index in range(1, size):
        current = candles[index]
        previous = candles[index - 1]
        up = float(current.high) - float(previous.high)
        down = float(previous.low) - float(current.low)
        plus_dm[index] = up if up > down and up > 0.0 else 0.0
        minus_dm[index] = down if down > up and down > 0.0 else 0.0
        tr[index] = max(
            float(current.high) - float(current.low),
            abs(float(current.high) - float(previous.close)),
            abs(float(current.low) - float(previous.close)),
        )
    atr_core = _rma(tr[1:], period)
    plus_core = _rma(plus_dm[1:], period)
    minus_core = _rma(minus_dm[1:], period)
    for offset, (atr, plus, minus) in enumerate(
        zip(atr_core, plus_core, minus_core, strict=True),
        start=1,
    ):
        if not _finite(atr, plus, minus) or float(atr) <= _EPS:
            continue
        plus_result[offset] = 100.0 * float(plus) / float(atr)
        minus_result[offset] = 100.0 * float(minus) / float(atr)
    return plus_result, minus_result


def _obv(candles: Sequence[BarObservation]) -> list[float]:
    if not candles:
        return []
    result = [0.0] * len(candles)
    for index in range(1, len(candles)):
        previous = float(candles[index - 1].close)
        current = float(candles[index].close)
        volume = max(0.0, float(candles[index].volume))
        direction = 1.0 if current > previous else -1.0 if current < previous else 0.0
        result[index] = result[index - 1] + direction * volume
    return result


def _snapshot(
    candles: Sequence[BarObservation],
    config: RouteConfig,
    index: int = -1,
) -> TrendSnapshot | None:
    minimum = max(205, int(config.trendrider_ema_slow) + 5)
    if len(candles) < minimum:
        return None
    closes = [float(candle.close) for candle in candles]
    volumes = [max(0.0, float(candle.volume)) for candle in candles]
    ema_fast = _ema(closes, int(config.trendrider_ema_fast))
    ema_slow = _ema(closes, int(config.trendrider_ema_slow))
    ema_50 = _ema(closes, 50)
    ema_200 = _ema(closes, 200)
    rsi = _rsi(closes, int(config.trendrider_rsi_period))
    adx = _adx(candles, 14)
    plus_di, minus_di = _di(candles, 14)
    macd_line, macd_signal = _macd(closes)
    macd_hist = [
        float(line) - float(signal)
        if _finite(line, signal) else math.nan
        for line, signal in zip(macd_line, macd_signal, strict=True)
    ]
    bb_middle = _sma(closes, 20)
    bb_std = _rolling_std(closes, 20)
    bb_upper = [
        float(mid) + 2.0 * float(std)
        if _finite(mid, std) else math.nan
        for mid, std in zip(bb_middle, bb_std, strict=True)
    ]
    bb_lower = [
        float(mid) - 2.0 * float(std)
        if _finite(mid, std) else math.nan
        for mid, std in zip(bb_middle, bb_std, strict=True)
    ]
    bb_width = [
        (float(upper) - float(lower)) / max(abs(float(mid)), _EPS)
        if _finite(upper, lower, mid) else math.nan
        for upper, lower, mid in zip(
            bb_upper,
            bb_lower,
            bb_middle,
            strict=True,
        )
    ]
    bb_width_sma = _sma([
        0.0 if not math.isfinite(value) else float(value)
        for value in bb_width
    ], 50)
    volume_ema = _ema(volumes, 20)
    volume_ratio = [
        float(volume) / max(float(mean), _EPS)
        if _finite(mean) else math.nan
        for volume, mean in zip(volumes, volume_ema, strict=True)
    ]
    obv = _obv(candles)
    obv_ema = _ema(obv, 20)
    resolved = index if index >= 0 else len(candles) + index
    if resolved <= 0 or resolved >= len(candles):
        return None
    required = (
        ema_fast[resolved],
        ema_slow[resolved],
        ema_50[resolved],
        ema_200[resolved],
        rsi[resolved],
        adx[resolved],
        plus_di[resolved],
        minus_di[resolved],
        macd_hist[resolved],
        macd_hist[resolved - 1],
        bb_upper[resolved],
        bb_middle[resolved],
        bb_lower[resolved],
        bb_width[resolved],
        bb_width_sma[resolved],
        volume_ratio[resolved],
        obv[resolved],
        obv_ema[resolved],
    )
    if not _finite(*required):
        return None
    candle = candles[resolved]
    bull = (
        float(candle.close) > float(ema_200[resolved])
        and float(ema_50[resolved]) > float(ema_200[resolved])
    )
    bear = (
        float(candle.close) < float(ema_200[resolved])
        and float(ema_50[resolved]) < float(ema_200[resolved])
    )
    return TrendSnapshot(
        ts_event=int(candle.ts_event),
        open=float(candle.open),
        high=float(candle.high),
        low=float(candle.low),
        close=float(candle.close),
        volume=float(candle.volume),
        ema_fast=float(ema_fast[resolved]),
        ema_slow=float(ema_slow[resolved]),
        ema_50=float(ema_50[resolved]),
        ema_200=float(ema_200[resolved]),
        rsi=float(rsi[resolved]),
        adx=float(adx[resolved]),
        plus_di=float(plus_di[resolved]),
        minus_di=float(minus_di[resolved]),
        macd_hist=float(macd_hist[resolved]),
        macd_hist_prev=float(macd_hist[resolved - 1]),
        bb_upper=float(bb_upper[resolved]),
        bb_middle=float(bb_middle[resolved]),
        bb_lower=float(bb_lower[resolved]),
        bb_width=float(bb_width[resolved]),
        bb_width_sma=float(bb_width_sma[resolved]),
        volume_ratio=float(volume_ratio[resolved]),
        obv=float(obv[resolved]),
        obv_ema=float(obv_ema[resolved]),
        is_bull=bool(bull),
        is_bear=bool(bear),
    )


def _context(
    symbol: str,
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    config: RouteConfig,
) -> tuple[list[BarObservation], TrendSnapshot | None, dict[str, float | int | str]]:
    hours = _aggregate_complete(
        bars_by_symbol.get(symbol, ()),
        int(config.trendrider_bucket_minutes),
    )
    snapshot = _snapshot(hours, config)

    four_hours = _aggregate_complete(bars_by_symbol.get(symbol, ()), 240)
    four_snapshot = _snapshot(four_hours, config) if len(four_hours) >= 205 else None
    is_bull_4h = int(four_snapshot.is_bull) if four_snapshot else 0
    adx_4h = float(four_snapshot.adx) if four_snapshot else 0.0

    days = _aggregate_complete(bars_by_symbol.get(symbol, ()), 1_440)
    daily_ema_200 = 0.0
    if len(days) >= 200:
        values = _ema([float(candle.close) for candle in days], 200)
        if values and _finite(values[-1]):
            daily_ema_200 = float(values[-1])

    btc_hours = _aggregate_complete(bars_by_symbol.get("BTCUSDT", ()), 60)
    btc_snapshot = _snapshot(btc_hours, config)
    btc_rsi = float(btc_snapshot.rsi) if btc_snapshot else 50.0
    btc_is_bull = int(btc_snapshot.is_bull) if btc_snapshot else 1

    return hours, snapshot, {
        "is_bull_4h": is_bull_4h,
        "adx_4h": adx_4h,
        "daily_ema_200": daily_ema_200,
        "btc_rsi_1h": btc_rsi,
        "btc_is_bull_1h": btc_is_bull,
        "private_fng_value": 50.0,
        "private_funding_rate": 0.0,
    }


def _confidence(
    snapshot: TrendSnapshot,
    context: Mapping[str, float | int | str],
    config: RouteConfig,
) -> tuple[int, tuple[str, ...]]:
    score = 0.0
    details: list[str] = []
    if 35.0 < snapshot.rsi < 60.0:
        score += 1.5
        details.append("RSI_HEALTHY")
    if snapshot.adx > 30.0:
        score += 2.5
        details.append("STRONG_TREND")
    elif snapshot.adx > float(config.trendrider_adx_threshold):
        score += 1.5
        details.append("MODERATE_TREND")
    if snapshot.volume_ratio > 1.5:
        score += 2.5
        details.append("HIGH_VOLUME")
    elif snapshot.volume_ratio > 1.0:
        score += 1.5
        details.append("NORMAL_VOLUME")
    if snapshot.macd_hist > 0.0:
        score += 1.5
        if snapshot.macd_hist > snapshot.macd_hist_prev:
            score += 0.5
            details.append("MACD_POSITIVE_RISING")
        else:
            details.append("MACD_POSITIVE")
    if snapshot.obv > snapshot.obv_ema:
        score += 1.5
        details.append("OBV_ABOVE_EMA")
    btc_rsi = float(context["btc_rsi_1h"])
    if 40.0 < btc_rsi < 70.0:
        score += 1.5
        details.append("BTC_HEALTHY")
    if int(context["is_bull_4h"]) == 1 and float(context["adx_4h"]) > 20.0:
        score += 1.5
        details.append("FOUR_HOUR_ALIGNED")
    bb_range = snapshot.bb_upper - snapshot.bb_lower
    if bb_range > _EPS:
        position = (snapshot.close - snapshot.bb_lower) / bb_range
        if position < 0.35:
            score += 1.0
            details.append("NEAR_BB_LOWER")
    if snapshot.plus_di - snapshot.minus_di > 10.0:
        score += 1.0
        details.append("STRONG_DI_SPREAD")
    # Public repository fixes omitted private inputs at neutral, granting the
    # same deterministic source bonuses.
    score += 1.0
    details.append("FNG_NEUTRAL_STUB")
    score += 1.0
    details.append("FUNDING_HEALTHY_STUB")
    numeric = max(1, min(10, int(round(score * 10.0 / 17.5))))
    return numeric, tuple(details)


def _regime(snapshot: TrendSnapshot) -> str:
    high_vol = (
        snapshot.bb_width > snapshot.bb_width_sma * 1.5
        if snapshot.bb_width_sma > 0.0 else False
    )
    if snapshot.adx < 20.0:
        return "RANGING_HIGH_VOL" if high_vol else "RANGING"
    if snapshot.is_bull and snapshot.close > snapshot.ema_200:
        return "TRENDING_BULL"
    return "TRENDING_BEAR_HIGH_VOL" if high_vol else "TRENDING_BEAR"


def evaluate_entry(
    symbol: str,
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    config: RouteConfig = RouteConfig(),
) -> EntryEvaluation:
    hours, snapshot, context = _context(symbol, bars_by_symbol, config)
    if snapshot is None or len(hours) < 206:
        return EntryEvaluation(
            None, 0, "UNRESOLVED", (), snapshot, context,
            "TRENDRIDER_HISTORY_NOT_READY",
        )
    previous = _snapshot(hours, config, -2)
    if previous is None:
        return EntryEvaluation(
            None, 0, "UNRESOLVED", (), snapshot, context,
            "TRENDRIDER_PREVIOUS_STATE_NOT_READY",
        )

    tags: list[str] = []
    fng_ok = True  # public neutral stub is 50
    btc_ok = float(context["btc_rsi_1h"]) > 35.0
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
        and fng_ok
        and snapshot.rsi < 70.0
        and snapshot.close > float(context["daily_ema_200"])
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
        and fng_ok
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
        and fng_ok
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
        and fng_ok
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
        and fng_ok
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
        and fng_ok
    ):
        tags.append("macd_reversal")

    confidence, details = _confidence(snapshot, context, config)
    regime = _regime(snapshot)
    if not tags:
        return EntryEvaluation(
            None, confidence, regime, details, snapshot, context,
            "TRENDRIDER_NO_PUBLIC_ENTRY_BRANCH",
        )
    minimum = (
        int(config.trendrider_min_confidence_bear)
        if "BEAR" in regime
        else int(config.trendrider_min_confidence_normal)
    )
    if confidence < minimum:
        return EntryEvaluation(
            tags[-1], confidence, regime, details, snapshot, context,
            "TRENDRIDER_CONFIDENCE_REJECTED",
        )
    # Later dataframe assignments overwrite earlier tags in the public source.
    selected = max(tags, key=lambda tag: _TAG_PRIORITY[tag])
    return EntryEvaluation(
        selected, confidence, regime, details, snapshot, context, "",
    )


def trendrider_exit_signal(
    candles: Sequence[BarObservation],
    config: RouteConfig = RouteConfig(),
) -> str | None:
    snapshot = _snapshot(candles, config)
    previous = _snapshot(candles, config, -2)
    if snapshot is None or previous is None:
        return None
    reasons: list[str] = []
    if snapshot.rsi > float(config.trendrider_rsi_exit) and snapshot.volume > 0.0:
        reasons.append("RSI_OVERBOUGHT")
    if (
        snapshot.ema_fast < snapshot.ema_slow
        and previous.ema_fast >= previous.ema_slow
        and snapshot.macd_hist < 0.0
        and snapshot.rsi > 50.0
        and snapshot.volume > 0.0
    ):
        reasons.append("EMA_BEARISH_CROSS")
    if (
        snapshot.close < snapshot.ema_200 * 0.99
        and previous.close >= previous.ema_200
        and snapshot.volume > 0.0
    ):
        reasons.append("TREND_BROKEN")
    if (
        snapshot.close < snapshot.ema_200 * 0.995
        and snapshot.rsi > 72.0
        and snapshot.macd_hist < snapshot.macd_hist_prev
        and snapshot.volume > 0.0
    ):
        reasons.append("TREND_EARLY_WARNING")
    return reasons[-1] if reasons else None


def classify_symbol(
    symbol: str,
    bars: Sequence[BarObservation],
    feature: FeatureObservation,
    config: RouteConfig = RouteConfig(),
) -> RouteDecision:
    del bars, feature
    return _unresolved(symbol, "TRENDRIDER_REQUIRES_UNIVERSE_CONTEXT")


classify_sma_offset = classify_symbol


def route_universe(
    bars_by_symbol: Mapping[str, Sequence[BarObservation]],
    features_by_symbol: Mapping[str, FeatureObservation],
    config: RouteConfig = RouteConfig(),
) -> tuple[RouteDecision | None, dict[str, RouteDecision]]:
    del features_by_symbol
    decisions: dict[str, RouteDecision] = {}
    for symbol in bars_by_symbol:
        evaluation = evaluate_entry(symbol, bars_by_symbol, config)
        snapshot = evaluation.snapshot
        episode_ts = int(snapshot.ts_event) if snapshot else 0
        diagnostics: dict[str, float | int | str] = {
            "entry_tag": evaluation.tag or "",
            "confidence": int(evaluation.confidence),
            "regime": evaluation.regime,
            "confidence_details": "|".join(evaluation.details),
            "reject_reason": evaluation.reject_reason,
            **dict(evaluation.context),
        }
        if snapshot is not None:
            diagnostics.update({
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
            })
        if evaluation.reject_reason:
            decisions[symbol] = _unresolved(
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
            decisions[symbol] = _unresolved(
                symbol,
                "TRENDRIDER_GEOMETRY_INVALID",
                episode_ts,
                diagnostics,
            )
            continue
        reward_r = (objective - entry) / (entry - stop)
        diagnostics["reward_r"] = reward_r
        decisions[symbol] = RouteDecision(
            symbol=symbol,
            state=TRENDRIDER_STATE,
            side=1,
            score=float(evaluation.confidence)
            + 0.01 * float(_TAG_PRIORITY[evaluation.tag]),
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
    actionable = [decision for decision in decisions.values() if decision.actionable]
    actionable.sort(key=lambda decision: (
        -decision.score,
        _SYMBOL_PRIORITY.get(decision.symbol, 99),
        decision.episode_ts,
    ))
    return (actionable[0] if actionable else None), decisions


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
    "evaluate_entry",
    "route_universe",
    "trendrider_exit_signal",
]
