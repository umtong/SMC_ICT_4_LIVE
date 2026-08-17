"""Causal feature parity for the EasyChart RE1 ML3 meta-router.

The deterministic EasyChart engines remain responsible for market geometry,
entry, invalidation and objective. This module converts a completed trade plan
and only already-completed one-minute bars into a compact, symbol-agnostic state
vector. The same transformation is used by the offline trainer and the live /
backtest strategy so that the model never receives a feature at deployment that
was defined differently during research.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import statistics
from typing import Any, Iterable, Mapping


FEATURE_SCHEMA_VERSION = "easychart-re1-ml3-features-v1"
OTHER_CATEGORY = "__OTHER__"
HORIZONS = (5, 15, 30, 60, 90, 240)
REQUIRED_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
NS_PER_MINUTE = 60_000_000_000

BASE_NUMERIC_FEATURES = (
    "gross_rr",
    "risk_bps",
    "target_bps",
    "risk_in_prior_sigma",
    "target_in_prior_sigma",
    "risk_in_prior_range",
    "target_in_prior_range",
    "higher_strength_ratio",
    "lower_strength_ratio",
    "trigger_strength_ratio",
    "source_rule_count",
    "overlap_width_bps",
    "interaction_to_observation_minutes",
    "trigger_to_observation_minutes",
    "setup_to_observation_minutes",
    "aligned_close_location_1m",
    "aligned_body_fraction_1m",
    "local_range_fraction_1m",
)
SEQUENTIAL_NUMERIC_FEATURES = tuple(
    name
    for minutes in HORIZONS
    for name in (
        f"aligned_seq_{minutes}m_return_z",
        f"aligned_seq_{minutes}m_path_efficiency",
        f"seq_{minutes}m_turn_rate",
        f"aligned_common_seq_{minutes}m_return_z",
        f"aligned_residual_seq_{minutes}m_return_z",
        f"aligned_common_seq_{minutes}m_breadth",
    )
)
NUMERIC_FEATURES = BASE_NUMERIC_FEATURES + SEQUENTIAL_NUMERIC_FEATURES
CATEGORICAL_FEATURES = (
    "family",
    "scenario_path",
    "scale_name",
    "higher_zone_kind",
    "lower_zone_kind",
    "trigger_zone_kind",
    "target_zone_kind",
)

FORBIDDEN_FEATURE_PREFIXES = (
    "counterfactual_",
    "mfe_",
    "mae_",
    "future_",
    "resolution_",
)
FORBIDDEN_FEATURE_NAMES = {
    "counterfactual_outcome",
    "economic_geometry_viable",
    "post_cost_break_even_target_probability",
    "counterfactual_target_net_r",
    "counterfactual_stop_net_r",
    "counterfactual_net_r_conservative",
    "zero_drift_target_first_prior",
    "zero_drift_expected_net_r",
    "required_target_probability_premium",
}


class FeatureUnavailable(RuntimeError):
    """Raised when a required causal feature cannot be produced."""


@dataclass(frozen=True, slots=True)
class MinuteBar:
    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close, self.volume)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("minute bar contains non-finite values")
        if self.ts_ns <= 0 or self.close <= 0.0 or self.high < self.low:
            raise ValueError("invalid minute bar")


def _as_text(value: Any) -> str:
    if value is None:
        raise FeatureUnavailable("categorical feature is missing")
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return str(enum_value)
    enum_name = getattr(value, "name", None)
    if enum_name is not None:
        return str(enum_name)
    text = str(value)
    if not text:
        raise FeatureUnavailable("categorical feature is empty")
    return text


def _as_float(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise FeatureUnavailable(f"numeric feature {name!r} is missing") from exc
    if not math.isfinite(result):
        raise FeatureUnavailable(f"numeric feature {name!r} is not finite")
    return result


def _side_sign(value: Any) -> float:
    text = _as_text(value).upper()
    if text.endswith("LONG"):
        return 1.0
    if text.endswith("SHORT"):
        return -1.0
    raise FeatureUnavailable(f"unknown plan side {text!r}")


def _median(values: Iterable[float], name: str) -> float:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    if not clean:
        raise FeatureUnavailable(f"no finite values for {name}")
    return float(statistics.median(clean))


def _mapping_value(row: Mapping[str, Any], name: str) -> Any:
    try:
        return row[name]
    except KeyError as exc:
        raise FeatureUnavailable(f"required offline column {name!r} is missing") from exc


def _plan_value(plan: Any, name: str) -> Any:
    if isinstance(plan, Mapping):
        return _mapping_value(plan, name)
    if not hasattr(plan, name):
        raise FeatureUnavailable(f"plan field {name!r} is missing")
    return getattr(plan, name)


def validate_feature_schema() -> None:
    overlap = set(NUMERIC_FEATURES) & set(CATEGORICAL_FEATURES)
    if overlap:
        raise RuntimeError(f"numeric/categorical feature overlap: {sorted(overlap)}")
    for name in (*NUMERIC_FEATURES, *CATEGORICAL_FEATURES):
        if name in FORBIDDEN_FEATURE_NAMES or name.startswith(FORBIDDEN_FEATURE_PREFIXES):
            raise RuntimeError(f"future/outcome feature entered ML3 schema: {name}")


validate_feature_schema()


def _static_plan_features(plan: Any, dynamic: Mapping[str, float]) -> dict[str, float | str]:
    side_sign = _side_sign(_plan_value(plan, "side"))
    entry = _as_float(_plan_value(plan, "entry"), "entry")
    stop = _as_float(_plan_value(plan, "stop"), "stop")
    target = _as_float(_plan_value(plan, "target"), "target")
    if entry == 0.0:
        raise FeatureUnavailable("entry cannot be zero")
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk <= 0.0 or reward <= 0.0:
        raise FeatureUnavailable("plan risk and reward must be positive")

    sigma = _as_float(dynamic["seq_prior_sigma_1m"], "seq_prior_sigma_1m")
    prior_range = _as_float(
        dynamic["seq_prior_range_fraction_1m"],
        "seq_prior_range_fraction_1m",
    )
    if sigma <= 0.0 or prior_range <= 0.0:
        raise FeatureUnavailable("prior sigma/range must be positive")

    observed = _as_float(_plan_value(plan, "observed_time_ns"), "observed_time_ns")
    interaction = _as_float(
        _plan_value(plan, "interaction_time_ns"),
        "interaction_time_ns",
    )
    trigger = _as_float(_plan_value(plan, "trigger_time_ns"), "trigger_time_ns")
    setup_observed = _as_float(
        _plan_value(plan, "setup_observed_time_ns"),
        "setup_observed_time_ns",
    )
    overlap_lower = _as_float(_plan_value(plan, "overlap_lower"), "overlap_lower")
    overlap_upper = _as_float(_plan_value(plan, "overlap_upper"), "overlap_upper")

    output: dict[str, float | str] = {
        "gross_rr": _as_float(_plan_value(plan, "gross_rr"), "gross_rr"),
        "risk_bps": 10_000.0 * risk / abs(entry),
        "target_bps": 10_000.0 * reward / abs(entry),
        "risk_in_prior_sigma": risk / (abs(entry) * sigma),
        "target_in_prior_sigma": reward / (abs(entry) * sigma),
        "risk_in_prior_range": risk / (abs(entry) * prior_range),
        "target_in_prior_range": reward / (abs(entry) * prior_range),
        "higher_strength_ratio": _as_float(
            _plan_value(plan, "higher_strength_ratio"),
            "higher_strength_ratio",
        ),
        "lower_strength_ratio": _as_float(
            _plan_value(plan, "lower_strength_ratio"),
            "lower_strength_ratio",
        ),
        "trigger_strength_ratio": _as_float(
            _plan_value(plan, "trigger_strength_ratio"),
            "trigger_strength_ratio",
        ),
        "source_rule_count": _as_float(
            _plan_value(plan, "source_rule_count"),
            "source_rule_count",
        ),
        "overlap_width_bps": 10_000.0 * abs(overlap_upper - overlap_lower) / abs(entry),
        "interaction_to_observation_minutes": max(
            0.0,
            (observed - interaction) / NS_PER_MINUTE,
        ),
        "trigger_to_observation_minutes": max(
            0.0,
            (observed - trigger) / NS_PER_MINUTE,
        ),
        "setup_to_observation_minutes": max(
            0.0,
            (observed - setup_observed) / NS_PER_MINUTE,
        ),
        "aligned_close_location_1m": side_sign
        * (2.0 * _as_float(dynamic["local_close_location_1m"], "local_close_location_1m") - 1.0),
        "aligned_body_fraction_1m": side_sign
        * _as_float(dynamic["local_body_fraction_1m"], "local_body_fraction_1m"),
        "local_range_fraction_1m": _as_float(
            dynamic["local_range_fraction_1m"],
            "local_range_fraction_1m",
        ),
        "family": _as_text(_plan_value(plan, "family")),
        "scenario_path": _as_text(_plan_value(plan, "scenario_path")),
        "scale_name": _as_text(_plan_value(plan, "scale_name")),
        "higher_zone_kind": _as_text(_plan_value(plan, "higher_zone_kind")),
        "lower_zone_kind": _as_text(_plan_value(plan, "lower_zone_kind")),
        "trigger_zone_kind": _as_text(_plan_value(plan, "trigger_zone_kind")),
        "target_zone_kind": _as_text(_plan_value(plan, "target_zone_kind")),
    }
    for minutes in HORIZONS:
        output[f"aligned_seq_{minutes}m_return_z"] = side_sign * _as_float(
            dynamic[f"seq_{minutes}m_return_z"],
            f"seq_{minutes}m_return_z",
        )
        output[f"aligned_seq_{minutes}m_path_efficiency"] = side_sign * _as_float(
            dynamic[f"seq_{minutes}m_path_efficiency"],
            f"seq_{minutes}m_path_efficiency",
        )
        output[f"seq_{minutes}m_turn_rate"] = _as_float(
            dynamic[f"seq_{minutes}m_turn_rate"],
            f"seq_{minutes}m_turn_rate",
        )
        output[f"aligned_common_seq_{minutes}m_return_z"] = side_sign * _as_float(
            dynamic[f"common_seq_{minutes}m_return_z"],
            f"common_seq_{minutes}m_return_z",
        )
        output[f"aligned_residual_seq_{minutes}m_return_z"] = side_sign * _as_float(
            dynamic[f"residual_seq_{minutes}m_return_z"],
            f"residual_seq_{minutes}m_return_z",
        )
        breadth_name = (
            f"common_seq_{minutes}m_positive_breadth"
            if side_sign > 0.0
            else f"common_seq_{minutes}m_negative_breadth"
        )
        output[f"aligned_common_seq_{minutes}m_breadth"] = _as_float(
            dynamic[breadth_name],
            breadth_name,
        )

    missing = [name for name in (*NUMERIC_FEATURES, *CATEGORICAL_FEATURES) if name not in output]
    if missing:
        raise RuntimeError(f"feature builder omitted schema fields: {missing}")
    return output


def offline_feature_row(row: Mapping[str, Any]) -> dict[str, float | str]:
    """Create the deployment feature vector from a harvested research row."""
    dynamic: dict[str, float] = {
        "seq_prior_sigma_1m": _as_float(
            _mapping_value(row, "seq_prior_sigma_1m"),
            "seq_prior_sigma_1m",
        ),
        "seq_prior_range_fraction_1m": _as_float(
            _mapping_value(row, "seq_prior_range_fraction_1m"),
            "seq_prior_range_fraction_1m",
        ),
        "local_close_location_1m": _as_float(
            _mapping_value(row, "local_close_location_1m"),
            "local_close_location_1m",
        ),
        "local_body_fraction_1m": _as_float(
            _mapping_value(row, "local_body_fraction_1m"),
            "local_body_fraction_1m",
        ),
        "local_range_fraction_1m": _as_float(
            _mapping_value(row, "local_range_fraction_1m"),
            "local_range_fraction_1m",
        ),
    }
    for minutes in HORIZONS:
        for name in (
            f"seq_{minutes}m_return_z",
            f"seq_{minutes}m_path_efficiency",
            f"seq_{minutes}m_turn_rate",
            f"common_seq_{minutes}m_return_z",
            f"residual_seq_{minutes}m_return_z",
            f"common_seq_{minutes}m_positive_breadth",
            f"common_seq_{minutes}m_negative_breadth",
        ):
            dynamic[name] = _as_float(_mapping_value(row, name), name)
    return _static_plan_features(row, dynamic)


class CausalOHLCVState:
    """Completed-minute state with an explicit four-symbol watermark."""

    def __init__(
        self,
        symbols: Iterable[str] = REQUIRED_SYMBOLS,
        *,
        maximum_bars: int = 1600,
    ) -> None:
        ordered = tuple(symbols)
        if len(set(ordered)) != len(ordered):
            raise ValueError("duplicate symbols")
        if maximum_bars < max(HORIZONS) + 3:
            raise ValueError("maximum_bars is too small for ML3 horizons")
        self.symbols = ordered
        self.maximum_bars = maximum_bars
        self.history: dict[str, deque[MinuteBar]] = {
            symbol: deque(maxlen=maximum_bars) for symbol in ordered
        }
        self.watermark_ns: int | None = None
        self.gap_resets: dict[str, int] = {symbol: 0 for symbol in ordered}

    def observe_synchronized(self, bars: Mapping[str, MinuteBar]) -> None:
        if set(bars) != set(self.symbols):
            missing = sorted(set(self.symbols) - set(bars))
            extra = sorted(set(bars) - set(self.symbols))
            raise FeatureUnavailable(
                f"ML3 synchronized bucket mismatch; missing={missing}, extra={extra}"
            )
        timestamps = {bar.ts_ns for bar in bars.values()}
        if len(timestamps) != 1:
            raise FeatureUnavailable("ML3 one-minute bars do not share one completed timestamp")
        timestamp = next(iter(timestamps))
        if self.watermark_ns is not None and timestamp <= self.watermark_ns:
            raise FeatureUnavailable(
                f"ML3 watermark did not advance: {timestamp} <= {self.watermark_ns}"
            )
        for symbol in self.symbols:
            history = self.history[symbol]
            bar = bars[symbol]
            if history and bar.ts_ns != history[-1].ts_ns + NS_PER_MINUTE:
                history.clear()
                self.gap_resets[symbol] += 1
            history.append(bar)
        self.watermark_ns = timestamp

    @staticmethod
    def _local_snapshot(history: deque[MinuteBar]) -> dict[str, float]:
        if len(history) < max(HORIZONS) + 3:
            raise FeatureUnavailable(
                f"ML3 requires at least {max(HORIZONS) + 3} contiguous completed one-minute bars"
            )
        bars = list(history)
        closes = [bar.close for bar in bars]
        log_closes = [math.log(value) for value in closes]
        returns = [log_closes[index] - log_closes[index - 1] for index in range(1, len(log_closes))]
        if len(returns) < max(HORIZONS) + 2:
            raise FeatureUnavailable("insufficient one-minute returns")

        prior_returns = returns[:-1][-1440:]
        sigma = _median((abs(value) for value in prior_returns), "prior sigma")
        sigma = max(sigma, 1e-12)
        prior_ranges = [
            (bar.high - bar.low) / bar.close
            for bar in bars[:-1][-1440:]
            if bar.close > 0.0
        ]
        prior_range = max(_median(prior_ranges, "prior range"), 1e-12)
        current = bars[-1]
        candle_range = current.high - current.low
        close_location = 0.5 if candle_range <= 0.0 else (current.close - current.low) / candle_range
        body_fraction = 0.0 if candle_range <= 0.0 else (current.close - current.open) / candle_range

        output: dict[str, float] = {
            "seq_prior_sigma_1m": sigma,
            "seq_prior_range_fraction_1m": prior_range,
            "local_close_location_1m": close_location,
            "local_body_fraction_1m": body_fraction,
            "local_range_fraction_1m": candle_range / current.close,
        }
        directions = [1.0 if value > 0.0 else -1.0 if value < 0.0 else 0.0 for value in returns]
        turns: list[float | None] = [None]
        for previous, current_direction in zip(directions, directions[1:]):
            turns.append(
                None
                if previous == 0.0 or current_direction == 0.0
                else float(previous != current_direction)
            )

        for minutes in HORIZONS:
            window = returns[-minutes:]
            if len(window) != minutes:
                raise FeatureUnavailable(f"insufficient {minutes}m return window")
            net = log_closes[-1] - log_closes[-1 - minutes]
            variation = sum(abs(value) for value in window)
            output[f"seq_{minutes}m_return_z"] = net / (sigma * math.sqrt(minutes))
            output[f"seq_{minutes}m_path_efficiency"] = 0.0 if variation <= 0.0 else net / variation
            turn_window = turns[-minutes:]
            valid_turns = [value for value in turn_window if value is not None]
            if len(valid_turns) < max(2, minutes - 1):
                raise FeatureUnavailable(f"insufficient directed observations for {minutes}m turn rate")
            output[f"seq_{minutes}m_turn_rate"] = sum(valid_turns) / len(valid_turns)
        return output

    def _dynamic_snapshot(self, symbol: str) -> dict[str, float]:
        if symbol not in self.history:
            raise FeatureUnavailable(f"unknown ML3 symbol {symbol!r}")
        if self.watermark_ns is None:
            raise FeatureUnavailable("ML3 has no completed four-symbol watermark")
        local = {
            item: self._local_snapshot(self.history[item])
            for item in self.symbols
        }
        output = dict(local[symbol])
        for minutes in HORIZONS:
            return_name = f"seq_{minutes}m_return_z"
            common_return = _median(
                (features[return_name] for features in local.values()),
                f"common {return_name}",
            )
            output[f"common_seq_{minutes}m_return_z"] = common_return
            output[f"residual_seq_{minutes}m_return_z"] = output[return_name] - common_return
            values = [features[return_name] for features in local.values()]
            output[f"common_seq_{minutes}m_positive_breadth"] = sum(
                value > 0.0 for value in values
            ) / len(values)
            output[f"common_seq_{minutes}m_negative_breadth"] = sum(
                value < 0.0 for value in values
            ) / len(values)
        return output

    def plan_features(self, plan: Any) -> dict[str, float | str]:
        symbol = _as_text(_plan_value(plan, "symbol"))
        return _static_plan_features(plan, self._dynamic_snapshot(symbol))


__all__ = [
    "BASE_NUMERIC_FEATURES",
    "CATEGORICAL_FEATURES",
    "CausalOHLCVState",
    "FEATURE_SCHEMA_VERSION",
    "FORBIDDEN_FEATURE_NAMES",
    "FORBIDDEN_FEATURE_PREFIXES",
    "FeatureUnavailable",
    "HORIZONS",
    "MinuteBar",
    "NUMERIC_FEATURES",
    "OTHER_CATEGORY",
    "REQUIRED_SYMBOLS",
    "offline_feature_row",
    "validate_feature_schema",
]
