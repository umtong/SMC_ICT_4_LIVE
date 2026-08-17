"""Causal, symbol-agnostic EasyChart ML2 features.

The features translate the EasyChart material into observable auction state:
meaningful location, rejection versus acceptance, first response quality,
aggressor-flow conversion, objective path and multi-timeframe context.  No raw
symbol identity is present.  Rolling baselines use only bars completed before
the current bar; the completed decision bucket itself is then available to the
plan emitted from that bucket.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
from statistics import median, pstdev
from typing import Any, Callable, Iterable, Mapping


TIMEFRAMES = (1, 5, 15, 60)
CAUSAL_FAMILIES = (
    "SWEEP_RECLAIM",
    "ACCEPTED_BREAK",
    "RANGE_ROTATION",
    "OTHER",
)

PLAN_FEATURES = (
    "plan_side",
    "gross_rr_log",
    "risk_bps_log",
    "target_bps_log",
    "higher_strength",
    "lower_strength",
    "trigger_strength",
    "confluence_strength",
    "higher_tf_log",
    "decision_tf_log",
    "trigger_tf_log",
    "scale_compression_log",
    "overlap_bps_log",
    "overlap_to_risk",
    "setup_to_interaction_minutes_log",
    "interaction_to_trigger_minutes_log",
    "trigger_to_observed_minutes_log",
    "higher_lower_same_kind",
)
FAMILY_FEATURES = tuple(f"family_{name.lower()}" for name in CAUSAL_FAMILIES)
SCENARIO_FEATURES = (
    "scenario_acceptance",
    "scenario_rejection",
    "scenario_rotation",
    "scenario_bounce",
)
MECHANISM_FEATURES = (
    "mechanism_order_block",
    "mechanism_fvg",
    "mechanism_horizontal",
    "mechanism_diagonal",
    "mechanism_channel",
    "mechanism_liquidity_sweep",
    "mechanism_retest",
    "mechanism_flow",
    "mechanism_pullback",
    "mechanism_continuation",
    "mechanism_rejection",
)
CONTEXT_FEATURES = (
    "macro_neutral",
    "macro_aligned",
    "macro_opposed",
    "factor_active",
    "factor_aligned",
    "factor_opposed",
    "factor_breadth",
    "factor_age_minutes_log",
    "setup_factor_active",
    "setup_factor_aligned",
    "setup_factor_opposed",
    "pre_response_factor_active",
    "pre_response_factor_aligned",
    "pre_response_factor_opposed",
)
TIME_FEATURES = (
    "utc_day_sin",
    "utc_day_cos",
    "utc_week_sin",
    "utc_week_cos",
)
ZONE_KIND_CLASSES = (
    "order_block",
    "fvg",
    "horizontal",
    "diagonal",
    "channel",
    "swing",
    "other",
)
ZONE_KIND_FEATURES = tuple(
    f"zone_{role}_kind_{kind}"
    for role in ("higher", "lower", "trigger", "target")
    for kind in ZONE_KIND_CLASSES
)
FLOW_FEATURES = (
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
)
ZONE_ROLES = ("higher", "lower", "trigger", "target")
ZONE_SUFFIXES = (
    "available",
    "age_minutes_log",
    "width_to_risk",
    "strength",
    "first_touch_seen",
    "distance_to_entry_r",
)
ZONE_FEATURES = tuple(
    f"zone_{role}_{suffix}"
    for role in ZONE_ROLES
    for suffix in ZONE_SUFFIXES
)
BAR_SUFFIXES = (
    "available",
    "history_fraction",
    "side_return_z",
    "side_body_fraction",
    "range_ratio_log",
    "volume_ratio_log",
    "side_close_location",
    "side_rejection_wick",
    "side_trend_20_z",
)
BAR_FEATURES = tuple(
    f"tf{timeframe}_{suffix}"
    for timeframe in TIMEFRAMES
    for suffix in BAR_SUFFIXES
)
CROSS_FEATURES = (
    "cross_available",
    "cross_common_return_z_side",
    "cross_return_dispersion_z",
    "cross_residual_z_side",
    "cross_same_side_breadth",
    "cross_btc_eth_side_alignment",
    "cross_relative_rank_side",
    "cross_common_volume_ratio_log",
)
INTERACTION_FEATURES = (
    "mechanism_confluence_log",
    "acceptance_x_macro_aligned",
    "acceptance_x_factor_aligned",
    "rejection_x_flow_absorption",
    "continuation_x_flow_initiative",
    "target_fresh_x_rr",
)

FEATURE_NAMES = (
    PLAN_FEATURES
    + FAMILY_FEATURES
    + SCENARIO_FEATURES
    + MECHANISM_FEATURES
    + CONTEXT_FEATURES
    + TIME_FEATURES
    + FLOW_FEATURES
    + ZONE_FEATURES
    + ZONE_KIND_FEATURES
    + BAR_FEATURES
    + CROSS_FEATURES
    + INTERACTION_FEATURES
)
FEATURE_DEFAULTS = {name: 0.0 for name in FEATURE_NAMES}
FEATURE_CLIP_RANGES = {name: (-12.0, 12.0) for name in FEATURE_NAMES}

for _name in FEATURE_NAMES:
    if (
        _name.endswith("available")
        or _name.endswith("aligned")
        or _name.endswith("opposed")
        or _name.endswith("neutral")
        or _name.endswith("active")
        or _name.endswith("seen")
        or _name.startswith("family_")
        or _name.startswith("scenario_")
        or _name.startswith("mechanism_")
        or "_kind_" in _name
    ):
        FEATURE_CLIP_RANGES[_name] = (0.0, 1.0)
for _name in (
    "plan_side",
    "utc_day_sin",
    "utc_day_cos",
    "utc_week_sin",
    "utc_week_cos",
    "flow_side_delta_share",
    "flow_side_body_fraction",
    "flow_side_close_location",
    "cross_same_side_breadth",
    "cross_btc_eth_side_alignment",
    "cross_relative_rank_side",
):
    FEATURE_CLIP_RANGES[_name] = (-1.0, 1.0)
for _name in (
    "factor_breadth",
    "higher_lower_same_kind",
    "flow_aligned_initiative",
    "flow_adverse_absorption_proxy",
    "acceptance_x_macro_aligned",
    "acceptance_x_factor_aligned",
    "rejection_x_flow_absorption",
    "continuation_x_flow_initiative",
):
    FEATURE_CLIP_RANGES[_name] = (0.0, 1.0)
FEATURE_CLIP_RANGES["mechanism_confluence_log"] = (0.0, 12.0)
for _name in FEATURE_NAMES:
    if _name.endswith("history_fraction") or _name.endswith("side_rejection_wick"):
        FEATURE_CLIP_RANGES[_name] = (0.0, 1.0)


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
    return _clip(math.log1p(max(0.0, _finite(value))), 0.0, 12.0)


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


def _plan_text(plan: Any) -> str:
    # Only the frozen plan's direct market objects are used.  The inherited
    # ``rule_provenance`` tuple contains the whole curriculum for many plans
    # and would falsely mark every mechanism as present.
    values = (
        getattr(plan, "family", ""),
        getattr(plan, "scenario_path", ""),
        getattr(plan, "scale_name", ""),
        getattr(plan, "higher_zone_kind", ""),
        getattr(plan, "lower_zone_kind", ""),
        getattr(plan, "trigger_zone_kind", ""),
        getattr(plan, "target_zone_kind", ""),
    )
    return "|".join(_token(value) for value in values)


def classify_plan_family(plan: Any) -> str:
    """Map implementation-specific names to three reusable causal episodes."""

    text = _plan_text(plan)
    scenario = _token(getattr(plan, "scenario_path", ""))
    # Explicit path ownership comes first.  A range rotation can occur after a
    # liquidity sweep, so the mere presence of the word SWEEP in provenance
    # must not relabel a completed ROTATION/BOUNCE episode as a reclaim trade.
    if (
        "ROTATION" in scenario
        or "BOUNCE" in scenario
        or "FOUR_POINT" in text
        or "4_POINT" in text
        or "RANGE_ROTATION" in text
        or "FAILURE_CHANNEL" in text
    ):
        return "RANGE_ROTATION"
    if (
        "REJECT" in scenario
        or "RECLAIM" in text
        or "FAKEOUT" in text
        or "FAKE_OUT" in text
        or "TRAP" in text
        or ("SWEEP" in text and "ACCEPT" not in scenario)
    ):
        return "SWEEP_RECLAIM"
    if (
        "ACCEPT" in scenario
        or "CONTINU" in text
        or "PULLBACK" in text
        or "BREAKOUT" in text
        or "S_R_FLIP" in text
        or "SR_FLIP" in text
        or "HORIZONTAL_FLIP" in text
    ):
        return "ACCEPTED_BREAK"
    return "OTHER"


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
    trend_20: float
    history_fraction: float


@dataclass(frozen=True, slots=True)
class CrossMarketObservation:
    ts_close_ns: int
    common_return_z: float
    return_dispersion_z: float
    residual_return_z: Mapping[str, float]
    relative_rank: Mapping[str, float]
    btc_eth_sign: float
    common_volume_ratio: float


class _BarState:
    def __init__(self, symbol: str, timeframe_minutes: int, *, maxlen: int = 512) -> None:
        self.symbol = symbol
        self.timeframe_minutes = timeframe_minutes
        self.history: deque[BarObservation] = deque(maxlen=maxlen)
        self.raw_volumes: deque[float] = deque(maxlen=maxlen)
        self.last_ts: int | None = None

    def observe(self, candle: Any) -> BarObservation:
        ts = int(getattr(candle, "ts_close_ns"))
        if self.last_ts is not None and ts <= self.last_ts:
            raise RuntimeError(
                f"non-increasing feature bar for {self.symbol} {self.timeframe_minutes}m: "
                f"{ts} <= {self.last_ts}",
            )
        open_price = _finite(getattr(candle, "open"))
        high = _finite(getattr(candle, "high"))
        low = _finite(getattr(candle, "low"))
        close = _finite(getattr(candle, "close"))
        if (
            open_price <= 0.0
            or close <= 0.0
            or high < max(open_price, close)
            or low > min(open_price, close)
        ):
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
        return_scale = (
            median(abs(item) for item in prior_returns)
            if prior_returns
            else max(abs(close_return), 1e-8)
        )
        median_range = median(prior_ranges) if prior_ranges else max(range_fraction, 1e-8)
        raw_volume = _finite(getattr(candle, "quote_volume", 0.0))
        if raw_volume <= 0.0:
            raw_volume = _finite(getattr(candle, "volume", 0.0))
        prior_volumes = list(self.raw_volumes)[-60:]
        median_volume = median(prior_volumes) if prior_volumes else max(raw_volume, 1e-12)
        trend_20 = math.log(close / prior[-20].close) if len(prior) >= 20 else close_return

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
            trend_20=trend_20,
            history_fraction=min(1.0, len(prior) / 60.0),
        )
        self.history.append(observation)
        self.raw_volumes.append(raw_volume)
        self.last_ts = ts
        return observation


class CausalFeatureBook:
    """Maintain prior-only rolling state and synchronized four-market context."""

    def __init__(self) -> None:
        self._states: dict[tuple[str, int], _BarState] = {}
        self._latest: dict[tuple[str, int], BarObservation] = {}
        self._cross: CrossMarketObservation | None = None

    def observe_bucket(self, items: Iterable[tuple[str, int, Any]]) -> None:
        current_one_minute: dict[str, BarObservation] = {}
        timestamps: set[int] = set()
        seen_keys: set[tuple[str, int]] = set()
        for symbol, timeframe, candle in sorted(items, key=lambda item: (item[1], item[0])):
            key = (str(symbol), int(timeframe))
            if key in seen_keys:
                raise RuntimeError(f"duplicate bar in feature bucket: {key}")
            seen_keys.add(key)
            state = self._states.setdefault(key, _BarState(*key))
            observation = state.observe(candle)
            self._latest[key] = observation
            timestamps.add(observation.ts_close_ns)
            if int(timeframe) == 1:
                current_one_minute[str(symbol)] = observation
        if len(timestamps) != 1:
            raise RuntimeError(f"feature bucket mixed close timestamps: {sorted(timestamps)}")
        self._cross = self._build_cross(current_one_minute)

    @staticmethod
    def _build_cross(
        current: Mapping[str, BarObservation],
    ) -> CrossMarketObservation | None:
        if len(current) < 2:
            return None
        z_values = {
            symbol: _safe_z(item.close_return, item.return_scale)
            for symbol, item in current.items()
        }
        common = median(z_values.values())
        dispersion = median(abs(value - common) for value in z_values.values())
        residual = {symbol: value - common for symbol, value in z_values.items()}
        ordered = sorted((value, symbol) for symbol, value in z_values.items())
        denominator = max(1, len(ordered) - 1)
        ranks = {
            symbol: 2.0 * rank / denominator - 1.0
            for rank, (_, symbol) in enumerate(ordered)
        }
        btc = z_values.get("BTCUSDT", 0.0)
        eth = z_values.get("ETHUSDT", 0.0)
        btc_eth_sign = 1.0 if btc > 0.0 and eth > 0.0 else -1.0 if btc < 0.0 and eth < 0.0 else 0.0
        return CrossMarketObservation(
            ts_close_ns=next(iter(current.values())).ts_close_ns,
            common_return_z=common,
            return_dispersion_z=dispersion,
            residual_return_z=residual,
            relative_rank=ranks,
            btc_eth_sign=btc_eth_sign,
            common_volume_ratio=median(item.volume_ratio for item in current.values()),
        )

    def latest(self, symbol: str, timeframe: int) -> BarObservation | None:
        return self._latest.get((symbol, timeframe))

    @property
    def cross(self) -> CrossMarketObservation | None:
        return self._cross


def _strength(plan: Any, modern: str, legacy: str) -> float:
    value = getattr(plan, modern, None)
    if value is None:
        value = getattr(plan, legacy, 0.0)
    return _clip(_finite(value), -12.0, 12.0)


def _macro_features(macro_side: Any, sign: float) -> dict[str, float]:
    if macro_side is None:
        return {"macro_neutral": 1.0, "macro_aligned": 0.0, "macro_opposed": 0.0}
    macro_sign = _side_sign(macro_side)
    return {
        "macro_neutral": 0.0,
        "macro_aligned": 1.0 if macro_sign == sign else 0.0,
        "macro_opposed": 1.0 if macro_sign != sign else 0.0,
    }


def _time_features(observed_time_ns: int) -> dict[str, float]:
    seconds = max(0.0, float(observed_time_ns) / 1_000_000_000.0)
    day_phase = 2.0 * math.pi * ((seconds % 86_400.0) / 86_400.0)
    week_phase = 2.0 * math.pi * ((seconds % 604_800.0) / 604_800.0)
    return {
        "utc_day_sin": math.sin(day_phase),
        "utc_day_cos": math.cos(day_phase),
        "utc_week_sin": math.sin(week_phase),
        "utc_week_cos": math.cos(week_phase),
    }


def _zone_kind_class(value: Any) -> str:
    text = _token(value)
    if "ORDER_BLOCK" in text or text == "OB" or text.endswith("_OB"):
        return "order_block"
    if "FVG" in text or "FAIR_VALUE" in text:
        return "fvg"
    if "HORIZONTAL" in text or "SUPPORT" in text or "RESISTANCE" in text:
        return "horizontal"
    if "TREND_LINE" in text or "TRENDLINE" in text or "DIAGONAL" in text:
        return "diagonal"
    if "CHANNEL" in text or "WEDGE" in text or "MIDLINE" in text:
        return "channel"
    if "SWING" in text or "PIVOT" in text:
        return "swing"
    return "other"


def _zone_kind_features(plan: Any) -> dict[str, float]:
    values = {
        "higher": getattr(plan, "higher_zone_kind", ""),
        "lower": getattr(plan, "lower_zone_kind", ""),
        "trigger": getattr(plan, "trigger_zone_kind", ""),
        "target": getattr(plan, "target_zone_kind", ""),
    }
    output = {name: 0.0 for name in ZONE_KIND_FEATURES}
    for role, value in values.items():
        output[f"zone_{role}_kind_{_zone_kind_class(value)}"] = 1.0
    return output


def _factor_features(plan: Any, factor_state: Any, sign: float) -> dict[str, float]:
    output = {
        "factor_active": 0.0,
        "factor_aligned": 0.0,
        "factor_opposed": 0.0,
        "factor_breadth": 0.0,
        "factor_age_minutes_log": 0.0,
    }
    if factor_state is None:
        return output
    factor_sign = _side_sign(getattr(factor_state, "side"))
    output["factor_active"] = 1.0
    output["factor_aligned"] = 1.0 if factor_sign == sign else 0.0
    output["factor_opposed"] = 1.0 if factor_sign != sign else 0.0
    agreeing = tuple(getattr(factor_state, "agreeing_symbols", ()) or ())
    output["factor_breadth"] = _clip(len(agreeing) / 4.0, 0.0, 1.0)
    output["factor_age_minutes_log"] = _safe_log1p(
        _minutes(
            int(getattr(plan, "observed_time_ns", 0))
            - int(getattr(factor_state, "event_time_ns", 0)),
        ),
    )
    return output


def _factor_snapshot_features(
    prefix: str,
    factor_state: Any,
    sign: float,
) -> dict[str, float]:
    output = {
        f"{prefix}_factor_active": 0.0,
        f"{prefix}_factor_aligned": 0.0,
        f"{prefix}_factor_opposed": 0.0,
    }
    if factor_state is None:
        return output
    factor_sign = _side_sign(getattr(factor_state, "side"))
    output[f"{prefix}_factor_active"] = 1.0
    output[f"{prefix}_factor_aligned"] = 1.0 if factor_sign == sign else 0.0
    output[f"{prefix}_factor_opposed"] = 1.0 if factor_sign != sign else 0.0
    return output


def _flow_features(flow: Any, sign: float) -> dict[str, float]:
    output = {name: 0.0 for name in FLOW_FEATURES}
    if flow is None:
        return output
    price_range = max(_finite(getattr(flow, "price_range", 0.0)), 1e-12)
    side_delta = sign * _finite(getattr(flow, "delta_share", 0.0))
    side_body = sign * _finite(getattr(flow, "body", 0.0)) / price_range
    location = _finite(getattr(flow, "close_location", 0.5), 0.5)
    side_location = 2.0 * (location if sign > 0.0 else 1.0 - location) - 1.0
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
            "flow_aligned_initiative": 1.0
            if active and directed and progress and side_delta > 0.0 and side_body > 0.0
            else 0.0,
            "flow_adverse_absorption_proxy": 1.0
            if active and directed and side_delta < 0.0 and side_body >= 0.0
            else 0.0,
        },
    )
    return output


def _mechanism_features(plan: Any) -> dict[str, float]:
    text = _plan_text(plan)
    keywords = {
        "mechanism_order_block": ("ORDER_BLOCK", "_OB", "OB_"),
        "mechanism_fvg": ("FVG", "FAIR_VALUE"),
        "mechanism_horizontal": ("HORIZONTAL", "S_R_FLIP", "SR_FLIP"),
        "mechanism_diagonal": ("DIAGONAL", "TRENDLINE", "TREND_LINE"),
        "mechanism_channel": ("CHANNEL", "WEDGE"),
        "mechanism_liquidity_sweep": ("SWEEP", "LIQUIDITY", "FAKEOUT", "FAKE_OUT", "TRAP"),
        "mechanism_retest": ("RETEST", "FIRST_RETURN", "RETURN"),
        "mechanism_flow": ("FLOW", "TAKER", "AGGRESSOR", "ABSORPTION", "INITIATIVE"),
        "mechanism_pullback": ("PULLBACK",),
        "mechanism_continuation": ("CONTINUATION", "ACCEPTANCE", "BREAKOUT"),
        "mechanism_rejection": ("REJECTION", "REVERSAL", "RECLAIM"),
    }
    return {
        name: 1.0 if any(keyword in text for keyword in values) else 0.0
        for name, values in keywords.items()
    }


def _zone_features(
    role: str,
    zone: Any,
    *,
    plan: Any,
    entry: float,
    risk: float,
) -> dict[str, float]:
    prefix = f"zone_{role}_"
    output = {prefix + suffix: 0.0 for suffix in ZONE_SUFFIXES}
    if zone is None:
        return output
    lower = _finite(getattr(zone, "lower", entry), entry)
    upper = _finite(getattr(zone, "upper", entry), entry)
    if upper < lower:
        lower, upper = upper, lower
    center = (lower + upper) / 2.0
    formed_time = int(
        getattr(zone, "formed_time_ns", getattr(zone, "observed_time_ns", 0)) or 0,
    )
    now = int(getattr(plan, "observed_time_ns", 0))
    first_touch = getattr(zone, "first_touch_time_ns", None)
    strength = _finite(getattr(zone, "strength_ratio", 0.0))
    output.update(
        {
            prefix + "available": 1.0,
            prefix + "age_minutes_log": _safe_log1p(_minutes(now - formed_time)),
            prefix + "width_to_risk": _clip(max(0.0, upper - lower) / risk, 0.0, 12.0),
            prefix + "strength": _clip(strength, -12.0, 12.0),
            prefix + "first_touch_seen": 1.0
            if first_touch is not None and int(first_touch) <= now
            else 0.0,
            prefix + "distance_to_entry_r": _clip(abs(entry - center) / risk, 0.0, 12.0),
        },
    )
    return output


def build_plan_features(
    plan: Any,
    *,
    feature_book: CausalFeatureBook,
    macro_side: Any = None,
    factor_state: Any = None,
    setup_factor_state: Any = None,
    pre_response_factor_state: Any = None,
    flow_observation: Any = None,
    zone_lookup: Callable[[str], Any | None] | None = None,
) -> tuple[str, dict[str, float]]:
    """Construct the exact runtime vector and its causal-family expert key."""

    features = dict(FEATURE_DEFAULTS)
    sign = _side_sign(getattr(plan, "side"))
    entry = _finite(getattr(plan, "entry"))
    stop = _finite(getattr(plan, "stop"))
    target = _finite(getattr(plan, "target"))
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if entry <= 0.0 or risk <= 0.0 or reward <= 0.0:
        raise ValueError(f"invalid plan geometry for {getattr(plan, 'plan_id', '<unknown>')}")

    higher_strength = _strength(plan, "higher_strength_ratio", "higher_zone_strength")
    lower_strength = _strength(plan, "lower_strength_ratio", "lower_zone_strength")
    trigger_strength = _strength(plan, "trigger_strength_ratio", "trigger_zone_strength")
    gross_rr = _finite(getattr(plan, "gross_rr", reward / risk), reward / risk)
    higher_tf = max(1.0, _finite(getattr(plan, "higher_timeframe_minutes", 60), 60.0))
    decision_tf = max(1.0, _finite(getattr(plan, "decision_timeframe_minutes", 15), 15.0))
    trigger_tf = max(1.0, _finite(getattr(plan, "trigger_timeframe_minutes", 1), 1.0))
    overlap_lower = _finite(getattr(plan, "overlap_lower", entry), entry)
    overlap_upper = _finite(getattr(plan, "overlap_upper", entry), entry)
    overlap = max(0.0, overlap_upper - overlap_lower)
    scenario = _token(getattr(plan, "scenario_path", ""))
    causal_family = classify_plan_family(plan)

    features.update(
        {
            "plan_side": sign,
            "gross_rr_log": _safe_log_ratio(gross_rr),
            "risk_bps_log": _safe_log1p(10_000.0 * risk / entry),
            "target_bps_log": _safe_log1p(10_000.0 * reward / entry),
            "higher_strength": higher_strength,
            "lower_strength": lower_strength,
            "trigger_strength": trigger_strength,
            "confluence_strength": _clip(
                higher_strength + lower_strength + trigger_strength,
                -12.0,
                12.0,
            ),
            "higher_tf_log": _safe_log1p(higher_tf),
            "decision_tf_log": _safe_log1p(decision_tf),
            "trigger_tf_log": _safe_log1p(trigger_tf),
            "scale_compression_log": _safe_log_ratio(higher_tf, trigger_tf),
            "overlap_bps_log": _safe_log1p(10_000.0 * overlap / entry),
            "overlap_to_risk": _clip(overlap / risk, 0.0, 12.0),
            "setup_to_interaction_minutes_log": _safe_log1p(
                _minutes(
                    int(getattr(plan, "interaction_time_ns", 0))
                    - int(getattr(plan, "setup_observed_time_ns", 0)),
                ),
            ),
            "interaction_to_trigger_minutes_log": _safe_log1p(
                _minutes(
                    int(getattr(plan, "trigger_time_ns", 0))
                    - int(getattr(plan, "interaction_time_ns", 0)),
                ),
            ),
            "trigger_to_observed_minutes_log": _safe_log1p(
                _minutes(
                    int(getattr(plan, "observed_time_ns", 0))
                    - int(getattr(plan, "trigger_time_ns", 0)),
                ),
            ),
            "higher_lower_same_kind": 1.0
            if _token(getattr(plan, "higher_zone_kind", ""))
            == _token(getattr(plan, "lower_zone_kind", ""))
            else 0.0,
            "scenario_acceptance": 1.0 if "ACCEPT" in scenario else 0.0,
            "scenario_rejection": 1.0 if "REJECT" in scenario else 0.0,
            "scenario_rotation": 1.0 if "ROTATION" in scenario else 0.0,
            "scenario_bounce": 1.0 if "BOUNCE" in scenario else 0.0,
        },
    )
    for family in CAUSAL_FAMILIES:
        features[f"family_{family.lower()}"] = 1.0 if causal_family == family else 0.0
    mechanism = _mechanism_features(plan)
    features.update(mechanism)
    features["mechanism_confluence_log"] = _safe_log1p(sum(mechanism.values()))
    features.update(_macro_features(macro_side, sign))
    features.update(_time_features(int(getattr(plan, "observed_time_ns", 0))))
    features.update(_zone_kind_features(plan))
    features.update(_factor_features(plan, factor_state, sign))
    features.update(_factor_snapshot_features("setup", setup_factor_state, sign))
    features.update(
        _factor_snapshot_features(
            "pre_response",
            pre_response_factor_state,
            sign,
        ),
    )
    features.update(_flow_features(flow_observation, sign))

    role_ids = {
        "higher": str(getattr(plan, "higher_zone_id", "")),
        "lower": str(getattr(plan, "lower_zone_id", "")),
        "trigger": str(getattr(plan, "trigger_zone_id", "")),
        "target": str(getattr(plan, "target_zone_id", "")),
    }
    for role, zone_id in role_ids.items():
        zone = None
        if zone_lookup is not None and zone_id:
            zone = zone_lookup(zone_id)
        features.update(
            _zone_features(role, zone, plan=plan, entry=entry, risk=risk),
        )

    symbol = str(getattr(plan, "symbol"))
    for timeframe in TIMEFRAMES:
        observation = feature_book.latest(symbol, timeframe)
        prefix = f"tf{timeframe}_"
        if observation is None:
            continue
        rejection_wick = (
            observation.lower_wick_fraction
            if sign > 0.0
            else observation.upper_wick_fraction
        )
        side_location = 2.0 * (
            observation.close_location
            if sign > 0.0
            else 1.0 - observation.close_location
        ) - 1.0
        features.update(
            {
                prefix + "available": 1.0,
                prefix + "history_fraction": observation.history_fraction,
                prefix + "side_return_z": _safe_z(
                    sign * observation.close_return,
                    observation.return_scale,
                ),
                prefix + "side_body_fraction": _clip(
                    sign * observation.body_fraction,
                    -1.0,
                    1.0,
                ),
                prefix + "range_ratio_log": _safe_log_ratio(observation.range_ratio),
                prefix + "volume_ratio_log": _safe_log_ratio(observation.volume_ratio),
                prefix + "side_close_location": _clip(side_location, -1.0, 1.0),
                prefix + "side_rejection_wick": _clip(rejection_wick, 0.0, 1.0),
                prefix + "side_trend_20_z": _safe_z(
                    sign * observation.trend_20,
                    observation.return_scale * math.sqrt(20.0),
                ),
            },
        )

    cross = feature_book.cross
    if cross is not None and symbol in cross.residual_return_z:
        market_returns = [
            value + cross.common_return_z
            for value in cross.residual_return_z.values()
        ]
        breadth = sum(value * sign > 0.0 for value in market_returns) / max(1, len(market_returns))
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
                "cross_same_side_breadth": _clip(2.0 * breadth - 1.0, -1.0, 1.0),
                "cross_btc_eth_side_alignment": _clip(sign * cross.btc_eth_sign, -1.0, 1.0),
                "cross_relative_rank_side": _clip(
                    sign * cross.relative_rank[symbol],
                    -1.0,
                    1.0,
                ),
                "cross_common_volume_ratio_log": _safe_log_ratio(cross.common_volume_ratio),
            },
        )

    features["acceptance_x_macro_aligned"] = features["scenario_acceptance"] * features["macro_aligned"]
    features["acceptance_x_factor_aligned"] = features["scenario_acceptance"] * features["factor_aligned"]
    features["rejection_x_flow_absorption"] = features["mechanism_rejection"] * features["flow_adverse_absorption_proxy"]
    features["continuation_x_flow_initiative"] = features["mechanism_continuation"] * features["flow_aligned_initiative"]
    # An unavailable zone is unknown, not fresh.  Freshness is evidence only
    # when the pre-existing target object was actually recovered from the
    # scenario engine.
    target_fresh = features["zone_target_available"] * (
        1.0 - features["zone_target_first_touch_seen"]
    )
    features["target_fresh_x_rr"] = _clip(target_fresh * gross_rr, 0.0, 12.0)

    missing = set(FEATURE_NAMES) - set(features)
    extra = set(features) - set(FEATURE_NAMES)
    if missing or extra:
        raise RuntimeError(
            f"feature schema mismatch missing={sorted(missing)} extra={sorted(extra)}",
        )
    return causal_family, {
        name: _clip(
            _finite(features[name]),
            *FEATURE_CLIP_RANGES.get(name, (-12.0, 12.0)),
        )
        for name in FEATURE_NAMES
    }
