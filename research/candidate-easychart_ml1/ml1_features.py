"""Causal, symbol-agnostic feature extraction for EasyChart ML1.

Every feature is available when the completed composite bar bucket is processed.
Rolling baselines are computed from prior bars only.  The four tradable symbols
share one feature definition; the model does not receive a symbol identifier.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
from statistics import median, pstdev
from typing import Any, Iterable, Mapping


TIMEFRAMES = (1, 5, 15, 60)
BAR_FEATURE_SUFFIXES = (
    "available",
    "history_fraction",
    "side_return_z",
    "side_body_fraction",
    "range_ratio_log",
    "volume_ratio_log",
    "side_close_location",
    "side_rejection_wick",
    "side_trend_5_z",
    "side_trend_20_z",
    "realized_vol_ratio_log",
)
MECHANISM_FEATURES = (
    "mechanism_order_block",
    "mechanism_fvg",
    "mechanism_horizontal",
    "mechanism_diagonal",
    "mechanism_channel",
    "mechanism_wedge",
    "mechanism_liquidity_sweep",
    "mechanism_retest",
    "mechanism_flow",
    "mechanism_pullback",
    "mechanism_macro",
    "mechanism_continuation",
    "mechanism_rejection",
)
ZONE_FEATURES = (
    "zone_order_block",
    "zone_fvg",
    "zone_horizontal",
    "zone_diagonal",
    "zone_channel",
    "zone_swing",
    "zone_liquidity",
)

BASE_FEATURE_NAMES = (
    "plan_side",
    "gross_rr_log",
    "risk_bps_log",
    "target_bps_log",
    "risk_to_prior_sigma_log",
    "target_to_prior_sigma_log",
    "risk_to_prior_range_log",
    "target_to_prior_range_log",
    "higher_strength",
    "lower_strength",
    "trigger_strength",
    "source_rule_count_log",
    "higher_tf_log",
    "decision_tf_log",
    "trigger_tf_log",
    "scale_compression_log",
    "overlap_bps_log",
    "overlap_to_risk",
    "setup_to_interaction_minutes_log",
    "interaction_to_trigger_minutes_log",
    "trigger_to_observed_minutes_log",
    "scenario_acceptance",
    "scenario_rejection",
    "higher_lower_same_kind",
    "macro_neutral",
    "macro_aligned",
    "macro_opposed",
    "factor_active",
    "factor_aligned",
    "factor_opposed",
    "factor_sequence_log",
    "factor_age_minutes_log",
    "factor_breadth",
    "flow_available",
    "flow_activity_ratio_log",
    "flow_delta_ratio_log",
    "flow_body_ratio_log",
    "flow_range_ratio_log",
    "flow_trade_size_ratio_log",
    "flow_impact_ratio_log",
    "flow_side_delta_share",
    "flow_side_body_fraction",
    "flow_side_close_location",
    "flow_aligned_initiative",
    "flow_adverse_absorption_proxy",
    "cross_available",
    "cross_common_return_z_side",
    "cross_return_dispersion_z",
    "cross_residual_z_side",
    "cross_same_side_breadth",
    "cross_btc_eth_side_alignment",
    "cross_relative_rank_side",
    "cross_common_volume_ratio_log",
    "acceptance_x_macro_aligned",
    "acceptance_x_factor_aligned",
    "rejection_x_flow_absorption",
    "continuation_x_flow_initiative",
    "confluence_strength",
)

FEATURE_NAMES = (
    BASE_FEATURE_NAMES
    + MECHANISM_FEATURES
    + ZONE_FEATURES
    + tuple(
        f"tf{timeframe}_{suffix}"
        for timeframe in TIMEFRAMES
        for suffix in BAR_FEATURE_SUFFIXES
    )
)
FEATURE_DEFAULTS = {name: 0.0 for name in FEATURE_NAMES}
FEATURE_CLIP_RANGES = {name: (-12.0, 12.0) for name in FEATURE_NAMES}
for _name in FEATURE_NAMES:
    if _name.endswith(("available", "aligned", "opposed", "neutral", "acceptance", "rejection")):
        FEATURE_CLIP_RANGES[_name] = (0.0, 1.0)
for _name in MECHANISM_FEATURES + ZONE_FEATURES:
    FEATURE_CLIP_RANGES[_name] = (0.0, 1.0)
for _name in (
    "plan_side",
    "flow_side_delta_share",
    "flow_side_body_fraction",
    "flow_side_close_location",
    "cross_common_return_z_side",
    "cross_residual_z_side",
    "cross_same_side_breadth",
    "cross_btc_eth_side_alignment",
    "cross_relative_rank_side",
):
    FEATURE_CLIP_RANGES[_name] = (-1.0, 1.0) if "_z_" not in _name else (-12.0, 12.0)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clip(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def _safe_log_ratio(value: float, baseline: float = 1.0) -> float:
    if value <= 0.0 or baseline <= 0.0:
        return 0.0
    return _clip(math.log(value / baseline), -12.0, 12.0)


def _safe_log1p(value: float) -> float:
    return math.log1p(max(0.0, _finite(value)))


def _safe_z(value: float, scale: float) -> float:
    return _clip(value / max(abs(scale), 1e-12), -12.0, 12.0)


def _side_sign(side: Any) -> float:
    text = str(getattr(side, "name", side)).upper()
    if text.endswith("LONG") or text == "BUY":
        return 1.0
    if text.endswith("SHORT") or text == "SELL":
        return -1.0
    raise ValueError(f"unknown side {side!r}")


def _token(value: Any) -> str:
    raw = getattr(value, "value", value)
    return "" if raw is None else str(raw).upper().replace("-", "_").replace(" ", "_")


def _minutes(delta_ns: int | float) -> float:
    return max(0.0, _finite(delta_ns) / 60_000_000_000.0)


@dataclass(frozen=True, slots=True)
class BarObservation:
    symbol: str
    timeframe_minutes: int
    ts_close_ns: int
    close: float
    close_return: float
    body_fraction: float
    range_fraction: float
    close_location: float
    upper_wick_fraction: float
    lower_wick_fraction: float
    range_ratio: float
    volume_ratio: float
    return_scale: float
    prior_noise_return_scale: float
    prior_noise_range_fraction: float
    realized_volatility: float
    prior_realized_volatility: float
    trend_5: float
    trend_20: float
    history_fraction: float


@dataclass(frozen=True, slots=True)
class CrossMarketObservation:
    ts_close_ns: int
    common_return_z: float
    return_dispersion_z: float
    residual_return_z: Mapping[str, float]
    same_side_breadth: Mapping[str, float]
    btc_eth_alignment: Mapping[str, float]
    relative_rank: Mapping[str, float]
    common_volume_ratio: float


class _BarState:
    def __init__(self, symbol: str, timeframe_minutes: int, *, maxlen: int = 1440) -> None:
        self.symbol = symbol
        self.timeframe_minutes = timeframe_minutes
        self.history: deque[BarObservation] = deque(maxlen=maxlen)
        self.last_ts: int | None = None

    def observe(self, candle: Any) -> BarObservation:
        ts = int(getattr(candle, "ts_close_ns"))
        if self.last_ts is not None and ts <= self.last_ts:
            raise RuntimeError(
                f"non-increasing feature bar for {self.symbol} {self.timeframe_minutes}m: {ts} <= {self.last_ts}",
            )
        open_price = _finite(getattr(candle, "open"))
        high = _finite(getattr(candle, "high"))
        low = _finite(getattr(candle, "low"))
        close = _finite(getattr(candle, "close"))
        if open_price <= 0.0 or close <= 0.0 or high < max(open_price, close) or low > min(open_price, close):
            raise ValueError("invalid candle geometry for feature extraction")
        price_range = max(high - low, open_price * 1e-12)
        body = close - open_price
        close_location = _clip((close - low) / price_range, 0.0, 1.0)
        upper_wick = max(0.0, high - max(open_price, close)) / price_range
        lower_wick = max(0.0, min(open_price, close) - low) / price_range
        range_fraction = price_range / open_price

        prior = list(self.history)
        prior_returns = [item.close_return for item in prior[-60:]]
        prior_ranges = [item.range_fraction for item in prior[-60:]]
        previous_close = prior[-1].close if prior else open_price
        close_return = math.log(close / max(previous_close, 1e-12))
        return_scale = median(abs(item) for item in prior_returns) if prior_returns else max(abs(close_return), 1e-8)
        median_range = median(prior_ranges) if prior_ranges else max(range_fraction, 1e-8)
        long_prior = prior[-1440:]
        long_return_scale = (
            median(abs(item.close_return) for item in long_prior)
            if long_prior
            else max(abs(close_return), 1e-8)
        )
        long_range_fraction = (
            median(item.range_fraction for item in long_prior)
            if long_prior
            else max(range_fraction, 1e-8)
        )

        raw_volume = _finite(getattr(candle, "quote_volume", 0.0))
        if raw_volume <= 0.0:
            raw_volume = _finite(getattr(candle, "volume", 0.0))
        prior_volumes = []
        # BarObservation stores the normalized ratio, not raw volume.  Preserve
        # the raw series separately through an attached deque-like attribute.
        if not hasattr(self, "_raw_volumes"):
            self._raw_volumes = deque(maxlen=512)  # type: ignore[attr-defined]
        prior_volumes = list(self._raw_volumes)[-60:]  # type: ignore[attr-defined]
        median_volume = median(prior_volumes) if prior_volumes else max(raw_volume, 1e-12)

        realized = pstdev(prior_returns[-20:] + [close_return]) if len(prior_returns) >= 2 else abs(close_return)
        prior_realized = pstdev(prior_returns[-20:]) if len(prior_returns) >= 2 else max(abs(close_return), 1e-8)
        trend_5 = math.log(close / prior[-5].close) if len(prior) >= 5 else close_return
        trend_20 = math.log(close / prior[-20].close) if len(prior) >= 20 else trend_5
        observation = BarObservation(
            symbol=self.symbol,
            timeframe_minutes=self.timeframe_minutes,
            ts_close_ns=ts,
            close=close,
            close_return=close_return,
            body_fraction=_clip(body / price_range, -1.0, 1.0),
            range_fraction=range_fraction,
            close_location=close_location,
            upper_wick_fraction=_clip(upper_wick, 0.0, 1.0),
            lower_wick_fraction=_clip(lower_wick, 0.0, 1.0),
            range_ratio=range_fraction / max(median_range, 1e-12),
            volume_ratio=raw_volume / max(median_volume, 1e-12),
            return_scale=max(return_scale, 1e-8),
            prior_noise_return_scale=max(long_return_scale, 1e-8),
            prior_noise_range_fraction=max(long_range_fraction, 1e-12),
            realized_volatility=max(realized, 1e-12),
            prior_realized_volatility=max(prior_realized, 1e-12),
            trend_5=trend_5,
            trend_20=trend_20,
            history_fraction=min(1.0, len(prior) / 60.0),
        )
        self.history.append(observation)
        self._raw_volumes.append(raw_volume)  # type: ignore[attr-defined]
        self.last_ts = ts
        return observation


class CausalFeatureBook:
    """Maintain prior-only bar state and synchronized four-symbol context."""

    def __init__(self) -> None:
        self._states: dict[tuple[str, int], _BarState] = {}
        self._latest: dict[tuple[str, int], BarObservation] = {}
        self._cross: CrossMarketObservation | None = None

    def observe_bucket(
        self,
        items: Iterable[tuple[str, int, Any]],
    ) -> None:
        current_one_minute: dict[str, BarObservation] = {}
        timestamps: set[int] = set()
        for symbol, timeframe, candle in sorted(items, key=lambda item: (item[1], item[0])):
            key = (symbol, int(timeframe))
            state = self._states.setdefault(key, _BarState(symbol, int(timeframe)))
            observation = state.observe(candle)
            self._latest[key] = observation
            timestamps.add(observation.ts_close_ns)
            if timeframe == 1:
                current_one_minute[symbol] = observation
        if len(timestamps) != 1:
            raise RuntimeError(f"feature bucket mixed close timestamps: {sorted(timestamps)}")
        self._cross = self._build_cross(current_one_minute)

    @staticmethod
    def _build_cross(current: Mapping[str, BarObservation]) -> CrossMarketObservation | None:
        if len(current) < 2:
            return None
        symbols = sorted(current)
        z_values = {
            symbol: _safe_z(item.close_return, item.return_scale)
            for symbol, item in current.items()
        }
        common = median(z_values.values())
        dispersion = median(abs(value - common) for value in z_values.values())
        residual = {symbol: value - common for symbol, value in z_values.items()}
        sorted_values = sorted((value, symbol) for symbol, value in z_values.items())
        rank_map: dict[str, float] = {}
        denominator = max(1, len(sorted_values) - 1)
        for rank, (_, symbol) in enumerate(sorted_values):
            rank_map[symbol] = 2.0 * rank / denominator - 1.0
        breadth_long = sum(value > 0.0 for value in z_values.values()) / len(z_values)
        breadth_short = sum(value < 0.0 for value in z_values.values()) / len(z_values)
        breadth = {
            symbol: breadth_long if z_values[symbol] >= 0.0 else breadth_short
            for symbol in symbols
        }
        btc = z_values.get("BTCUSDT", 0.0)
        eth = z_values.get("ETHUSDT", 0.0)
        leader_sign = 1.0 if btc > 0.0 and eth > 0.0 else -1.0 if btc < 0.0 and eth < 0.0 else 0.0
        alignment = {symbol: leader_sign for symbol in symbols}
        common_volume = median(item.volume_ratio for item in current.values())
        return CrossMarketObservation(
            ts_close_ns=next(iter(current.values())).ts_close_ns,
            common_return_z=common,
            return_dispersion_z=dispersion,
            residual_return_z=residual,
            same_side_breadth=breadth,
            btc_eth_alignment=alignment,
            relative_rank=rank_map,
            common_volume_ratio=common_volume,
        )

    def latest(self, symbol: str, timeframe: int) -> BarObservation | None:
        return self._latest.get((symbol, timeframe))

    @property
    def cross(self) -> CrossMarketObservation | None:
        return self._cross


def _factor_features(plan: Any, factor_state: Any, sign: float) -> dict[str, float]:
    output = {
        "factor_active": 0.0,
        "factor_aligned": 0.0,
        "factor_opposed": 0.0,
        "factor_sequence_log": 0.0,
        "factor_age_minutes_log": 0.0,
        "factor_breadth": 0.0,
    }
    if factor_state is None:
        return output
    factor_sign = _side_sign(getattr(factor_state, "side"))
    output["factor_active"] = 1.0
    output["factor_aligned"] = 1.0 if factor_sign == sign else 0.0
    output["factor_opposed"] = 1.0 if factor_sign != sign else 0.0
    output["factor_sequence_log"] = _safe_log1p(getattr(factor_state, "sequence", 0))
    output["factor_age_minutes_log"] = _safe_log1p(
        _minutes(int(getattr(plan, "observed_time_ns", 0)) - int(getattr(factor_state, "event_time_ns", 0))),
    )
    agreeing = getattr(factor_state, "agreeing_symbols", ()) or ()
    output["factor_breadth"] = len(tuple(agreeing)) / 4.0
    return output


def _macro_features(macro_side: Any, sign: float) -> dict[str, float]:
    if macro_side is None:
        return {"macro_neutral": 1.0, "macro_aligned": 0.0, "macro_opposed": 0.0}
    macro_sign = _side_sign(macro_side)
    return {
        "macro_neutral": 0.0,
        "macro_aligned": 1.0 if macro_sign == sign else 0.0,
        "macro_opposed": 1.0 if macro_sign != sign else 0.0,
    }


def _flow_features(flow: Any, sign: float) -> dict[str, float]:
    output = {
        "flow_available": 0.0,
        "flow_activity_ratio_log": 0.0,
        "flow_delta_ratio_log": 0.0,
        "flow_body_ratio_log": 0.0,
        "flow_range_ratio_log": 0.0,
        "flow_trade_size_ratio_log": 0.0,
        "flow_impact_ratio_log": 0.0,
        "flow_side_delta_share": 0.0,
        "flow_side_body_fraction": 0.0,
        "flow_side_close_location": 0.0,
        "flow_aligned_initiative": 0.0,
        "flow_adverse_absorption_proxy": 0.0,
    }
    if flow is None:
        return output
    price_range = max(_finite(getattr(flow, "price_range", 0.0)), 1e-12)
    side_delta = sign * _finite(getattr(flow, "delta_share", 0.0))
    side_body = sign * _finite(getattr(flow, "body", 0.0)) / price_range
    location = _finite(getattr(flow, "close_location", 0.5), 0.5)
    side_location = 2.0 * (location if sign > 0 else 1.0 - location) - 1.0
    active = bool(getattr(flow, "active", False))
    directed = bool(getattr(flow, "directed", False))
    progress = bool(getattr(flow, "material_progress", False))
    output.update(
        {
            "flow_available": 1.0,
            "flow_activity_ratio_log": _safe_log_ratio(_finite(getattr(flow, "activity_ratio", 1.0))),
            "flow_delta_ratio_log": _safe_log_ratio(_finite(getattr(flow, "delta_ratio", 1.0))),
            "flow_body_ratio_log": _safe_log_ratio(_finite(getattr(flow, "body_ratio", 1.0))),
            "flow_range_ratio_log": _safe_log_ratio(_finite(getattr(flow, "range_ratio", 1.0))),
            "flow_trade_size_ratio_log": _safe_log_ratio(_finite(getattr(flow, "trade_size_ratio", 1.0))),
            "flow_impact_ratio_log": _safe_log_ratio(_finite(getattr(flow, "impact_per_activity", 1.0))),
            "flow_side_delta_share": _clip(side_delta, -1.0, 1.0),
            "flow_side_body_fraction": _clip(side_body, -1.0, 1.0),
            "flow_side_close_location": _clip(side_location, -1.0, 1.0),
            "flow_aligned_initiative": 1.0 if active and directed and progress and side_delta > 0.0 and side_body > 0.0 else 0.0,
            "flow_adverse_absorption_proxy": 1.0 if active and directed and side_delta < 0.0 and side_body >= 0.0 else 0.0,
        },
    )
    return output


def _text_features(plan: Any) -> dict[str, float]:
    parts = [
        _token(getattr(plan, "family", "")),
        _token(getattr(plan, "scenario_path", "")),
        _token(getattr(plan, "higher_zone_kind", "")),
        _token(getattr(plan, "lower_zone_kind", "")),
        _token(getattr(plan, "trigger_zone_kind", "")),
        _token(getattr(plan, "target_zone_kind", "")),
        _token(getattr(plan, "scale_name", "")),
    ]
    # ``rule_provenance`` contains the repository-wide audit catalogue in
    # several V5 plans.  Parsing it as plan-local evidence marks unrelated
    # mechanisms as present.  Mechanism identity must come only from the frozen
    # plan's family, scenario, zones, trigger and scale.
    text = "|".join(parts)
    feature_keywords = {
        "mechanism_order_block": ("ORDER_BLOCK", "_OB", "OB_"),
        "mechanism_fvg": ("FVG", "FAIR_VALUE"),
        "mechanism_horizontal": ("HORIZONTAL", "S_R_FLIP", "SR_FLIP"),
        "mechanism_diagonal": ("DIAGONAL", "TRENDLINE", "TREND_LINE"),
        "mechanism_channel": ("CHANNEL",),
        "mechanism_wedge": ("WEDGE",),
        "mechanism_liquidity_sweep": ("SWEEP", "LIQUIDITY", "FAKEOUT", "FAKE_OUT", "TRAP"),
        "mechanism_retest": ("RETEST", "FIRST_RETURN", "RETURN"),
        "mechanism_flow": ("FLOW", "TAKER", "AGGRESSOR", "ABSORPTION", "INITIATIVE"),
        "mechanism_pullback": ("PULLBACK",),
        "mechanism_macro": ("MACRO", "SIXTY_MINUTE", "60M"),
        "mechanism_continuation": ("CONTINUATION", "ACCEPTANCE"),
        "mechanism_rejection": ("REJECTION", "REVERSAL", "RECLAIM"),
    }
    output = {
        name: 1.0 if any(keyword in text for keyword in keywords) else 0.0
        for name, keywords in feature_keywords.items()
    }
    zone_text = "|".join(parts[2:6])
    zone_keywords = {
        "zone_order_block": ("ORDER_BLOCK", "_OB", "OB_"),
        "zone_fvg": ("FVG", "FAIR_VALUE"),
        "zone_horizontal": ("HORIZONTAL", "SUPPORT", "RESISTANCE", "SR_"),
        "zone_diagonal": ("DIAGONAL", "TRENDLINE", "TREND_LINE"),
        "zone_channel": ("CHANNEL",),
        "zone_swing": ("SWING", "PIVOT", "HIGH", "LOW"),
        "zone_liquidity": ("LIQUIDITY", "SWEEP"),
    }
    output.update(
        {
            name: 1.0 if any(keyword in zone_text for keyword in keywords) else 0.0
            for name, keywords in zone_keywords.items()
        },
    )
    return output


def build_plan_features(
    plan: Any,
    *,
    feature_book: CausalFeatureBook,
    macro_side: Any = None,
    factor_state: Any = None,
    flow_observation: Any = None,
) -> dict[str, float]:
    """Construct the exact fixed feature vector for one frozen trade plan."""

    features = dict(FEATURE_DEFAULTS)
    sign = _side_sign(getattr(plan, "side"))
    entry = _finite(getattr(plan, "entry"))
    stop = _finite(getattr(plan, "stop"))
    target = _finite(getattr(plan, "target"))
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if entry <= 0.0 or risk <= 0.0 or reward <= 0.0:
        raise ValueError(f"invalid plan geometry for {getattr(plan, 'plan_id', '<unknown>')}")

    gross_rr = _finite(getattr(plan, "gross_rr", reward / risk), reward / risk)
    symbol = str(getattr(plan, "symbol"))
    one_minute = feature_book.latest(symbol, 1)
    if one_minute is None:
        risk_to_prior_sigma = 0.0
        target_to_prior_sigma = 0.0
        risk_to_prior_range = 0.0
        target_to_prior_range = 0.0
    else:
        risk_fraction = risk / entry
        target_fraction = reward / entry
        risk_to_prior_sigma = risk_fraction / max(one_minute.prior_noise_return_scale, 1e-12)
        target_to_prior_sigma = target_fraction / max(one_minute.prior_noise_return_scale, 1e-12)
        risk_to_prior_range = risk_fraction / max(one_minute.prior_noise_range_fraction, 1e-12)
        target_to_prior_range = target_fraction / max(one_minute.prior_noise_range_fraction, 1e-12)
    higher_tf = max(1.0, _finite(getattr(plan, "higher_timeframe_minutes", 60), 60.0))
    decision_tf = max(1.0, _finite(getattr(plan, "decision_timeframe_minutes", 15), 15.0))
    trigger_tf = max(1.0, _finite(getattr(plan, "trigger_timeframe_minutes", 1), 1.0))
    overlap_lower = _finite(getattr(plan, "overlap_lower", entry), entry)
    overlap_upper = _finite(getattr(plan, "overlap_upper", entry), entry)
    overlap = max(0.0, overlap_upper - overlap_lower)
    scenario = _token(getattr(plan, "scenario_path", ""))
    higher_kind = _token(getattr(plan, "higher_zone_kind", ""))
    lower_kind = _token(getattr(plan, "lower_zone_kind", ""))
    local_structure_ids = {
        str(value)
        for value in (
            getattr(plan, "higher_zone_id", ""),
            getattr(plan, "lower_zone_id", ""),
            getattr(plan, "trigger_zone_id", ""),
            getattr(plan, "target_zone_id", ""),
        )
        if value not in (None, "")
    }

    features.update(
        {
            "plan_side": sign,
            "gross_rr_log": _safe_log_ratio(gross_rr, 1.0),
            "risk_bps_log": _safe_log1p(10_000.0 * risk / entry),
            "target_bps_log": _safe_log1p(10_000.0 * reward / entry),
            "risk_to_prior_sigma_log": _safe_log1p(risk_to_prior_sigma),
            "target_to_prior_sigma_log": _safe_log1p(target_to_prior_sigma),
            "risk_to_prior_range_log": _safe_log1p(risk_to_prior_range),
            "target_to_prior_range_log": _safe_log1p(target_to_prior_range),
            "higher_strength": _clip(_finite(getattr(plan, "higher_strength_ratio", 0.0)), -12.0, 12.0),
            "lower_strength": _clip(_finite(getattr(plan, "lower_strength_ratio", 0.0)), -12.0, 12.0),
            "trigger_strength": _clip(_finite(getattr(plan, "trigger_strength_ratio", 0.0)), -12.0, 12.0),
            # Several plans carry the repository-wide provenance catalogue.
            # Its length is a code-version fingerprint, not trade confluence.
            # Count only distinct structures actually used by this frozen plan.
            "source_rule_count_log": _safe_log1p(len(local_structure_ids)),
            "higher_tf_log": _safe_log1p(higher_tf),
            "decision_tf_log": _safe_log1p(decision_tf),
            "trigger_tf_log": _safe_log1p(trigger_tf),
            "scale_compression_log": _safe_log_ratio(higher_tf, trigger_tf),
            "overlap_bps_log": _safe_log1p(10_000.0 * overlap / entry),
            "overlap_to_risk": _clip(overlap / risk, 0.0, 12.0),
            "setup_to_interaction_minutes_log": _safe_log1p(
                _minutes(int(getattr(plan, "interaction_time_ns", 0)) - int(getattr(plan, "setup_observed_time_ns", 0))),
            ),
            "interaction_to_trigger_minutes_log": _safe_log1p(
                _minutes(int(getattr(plan, "trigger_time_ns", 0)) - int(getattr(plan, "interaction_time_ns", 0))),
            ),
            "trigger_to_observed_minutes_log": _safe_log1p(
                _minutes(int(getattr(plan, "observed_time_ns", 0)) - int(getattr(plan, "trigger_time_ns", 0))),
            ),
            "scenario_acceptance": 1.0 if "ACCEPT" in scenario or "CONTINU" in scenario else 0.0,
            "scenario_rejection": 1.0 if "REJECT" in scenario or "REVERS" in scenario or "RECLAIM" in scenario else 0.0,
            "higher_lower_same_kind": 1.0 if higher_kind and higher_kind == lower_kind else 0.0,
        },
    )
    features.update(_text_features(plan))
    features.update(_macro_features(macro_side, sign))
    features.update(_factor_features(plan, factor_state, sign))
    features.update(_flow_features(flow_observation, sign))

    symbol = str(getattr(plan, "symbol"))
    for timeframe in TIMEFRAMES:
        observation = feature_book.latest(symbol, timeframe)
        prefix = f"tf{timeframe}_"
        if observation is None:
            continue
        rejection_wick = observation.lower_wick_fraction if sign > 0.0 else observation.upper_wick_fraction
        side_location = 2.0 * (
            observation.close_location if sign > 0.0 else 1.0 - observation.close_location
        ) - 1.0
        features.update(
            {
                prefix + "available": 1.0,
                prefix + "history_fraction": observation.history_fraction,
                prefix + "side_return_z": _safe_z(sign * observation.close_return, observation.return_scale),
                prefix + "side_body_fraction": _clip(sign * observation.body_fraction, -1.0, 1.0),
                prefix + "range_ratio_log": _safe_log_ratio(observation.range_ratio),
                prefix + "volume_ratio_log": _safe_log_ratio(observation.volume_ratio),
                prefix + "side_close_location": _clip(side_location, -1.0, 1.0),
                prefix + "side_rejection_wick": _clip(rejection_wick, 0.0, 1.0),
                prefix + "side_trend_5_z": _safe_z(sign * observation.trend_5, observation.return_scale * math.sqrt(5.0)),
                prefix + "side_trend_20_z": _safe_z(sign * observation.trend_20, observation.return_scale * math.sqrt(20.0)),
                prefix + "realized_vol_ratio_log": _safe_log_ratio(
                    observation.realized_volatility,
                    observation.prior_realized_volatility,
                ),
            },
        )

    cross = feature_book.cross
    if cross is not None and symbol in cross.residual_return_z:
        side_breadth = sum(
            1
            for value in cross.residual_return_z.values()
            if (value + cross.common_return_z) * sign > 0.0
        ) / max(1, len(cross.residual_return_z))
        leader = cross.btc_eth_alignment.get(symbol, 0.0)
        features.update(
            {
                "cross_available": 1.0,
                "cross_common_return_z_side": _clip(sign * cross.common_return_z, -12.0, 12.0),
                "cross_return_dispersion_z": _clip(cross.return_dispersion_z, 0.0, 12.0),
                "cross_residual_z_side": _clip(
                    sign * cross.residual_return_z[symbol],
                    -12.0,
                    12.0,
                ),
                "cross_same_side_breadth": _clip(2.0 * side_breadth - 1.0, -1.0, 1.0),
                "cross_btc_eth_side_alignment": _clip(sign * leader, -1.0, 1.0),
                "cross_relative_rank_side": _clip(sign * cross.relative_rank[symbol], -1.0, 1.0),
                "cross_common_volume_ratio_log": _safe_log_ratio(cross.common_volume_ratio),
            },
        )

    features["acceptance_x_macro_aligned"] = features["scenario_acceptance"] * features["macro_aligned"]
    features["acceptance_x_factor_aligned"] = features["scenario_acceptance"] * features["factor_aligned"]
    features["rejection_x_flow_absorption"] = features["scenario_rejection"] * features["flow_adverse_absorption_proxy"]
    features["continuation_x_flow_initiative"] = features["mechanism_continuation"] * features["flow_aligned_initiative"]
    features["confluence_strength"] = _clip(
        features["higher_strength"]
        + features["lower_strength"]
        + features["trigger_strength"]
        + 0.5 * sum(features[name] for name in MECHANISM_FEATURES),
        -12.0,
        12.0,
    )

    missing = set(FEATURE_NAMES) - set(features)
    extra = set(features) - set(FEATURE_NAMES)
    if missing or extra:
        raise RuntimeError(f"feature schema mismatch missing={sorted(missing)} extra={sorted(extra)}")
    return {
        name: _clip(_finite(features[name]), *FEATURE_CLIP_RANGES.get(name, (-12.0, 12.0)))
        for name in FEATURE_NAMES
    }
