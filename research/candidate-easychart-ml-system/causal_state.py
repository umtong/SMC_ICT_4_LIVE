"""Shared causal market-state features for ML research and live routing.

The same incremental state machine is used in two places:

* offline, to attach point-in-time state to counterfactual plans;
* online/backtest, immediately after the synchronized four-symbol one-minute
  bucket has closed and before plans from that bucket are scored.

No calendar date or symbol identity is a model feature. All normalizers use
only observations strictly prior to the current completed minute. Common
crypto fields are formed only after every configured symbol has reached the
same completed-minute watermark.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

NS_PER_MINUTE = 60_000_000_000
HORIZONS: tuple[int, ...] = (5, 15, 30, 60, 90, 240)
BASELINE_BARS = 1440
HISTORY_BARS = BASELINE_BARS + max(HORIZONS) + 10

STATE_POLICY = (
    "CAUSAL_SHARED_STATE:CURRENT_COMPLETED_ONE_MINUTE_BAR_PLUS_PRIOR_ONLY_ROBUST_"
    "NORMALIZERS_AND_SYNCHRONIZED_FOUR_SYMBOL_COMMON_STATE"
)


@dataclass(frozen=True, slots=True)
class MinuteObservation:
    ts_close_ns: int
    close: float
    log_close: float
    log_return: float
    quote_volume: float
    trade_count: float
    signed_taker_quote: float
    delta_share: float
    range_fraction: float
    body_fraction: float
    close_location_signed: float


def _finite(value: Any, default: float = float("nan")) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _safe_median(values: Iterable[float], default: float) -> float:
    finite = [float(item) for item in values if math.isfinite(float(item))]
    return median(finite) if finite else default


def _safe_ratio(numerator: float, denominator: float, floor: float = 1e-12) -> float:
    if not math.isfinite(numerator) or not math.isfinite(denominator):
        return float("nan")
    return numerator / max(abs(denominator), floor)


def _turn_rate(signs: Sequence[int]) -> float:
    if len(signs) < 2:
        return float("nan")
    valid = 0
    turns = 0
    previous = signs[0]
    for current in signs[1:]:
        if previous != 0 and current != 0:
            valid += 1
            turns += int(previous != current)
        previous = current
    return turns / valid if valid else float("nan")


def _std(values: Sequence[float]) -> float:
    finite = np.asarray([item for item in values if math.isfinite(item)], dtype=np.float64)
    return float(finite.std(ddof=0)) if finite.size else float("nan")


SIGNED_LOCAL_FEATURES: tuple[str, ...] = (
    "mls_body_signed_1m",
    "mls_close_location_signed_1m",
    "mls_delta_share_1m",
    *(f"mls_return_z_{n}m" for n in HORIZONS),
    *(f"mls_path_efficiency_{n}m" for n in HORIZONS),
    *(f"mls_delta_share_{n}m" for n in HORIZONS),
    *(f"mls_impact_efficiency_{n}m" for n in HORIZONS),
    *(f"mls_flow_progress_product_{n}m" for n in HORIZONS),
)

COMMON_SOURCE_FEATURES: tuple[str, ...] = (
    *(f"mls_return_z_{n}m" for n in HORIZONS),
    *(f"mls_path_efficiency_{n}m" for n in (15, 60, 240)),
    *(f"mls_delta_share_{n}m" for n in (5, 15, 60)),
    *(f"mls_impact_efficiency_{n}m" for n in (5, 15, 60)),
    "mls_volatility_ratio_30_240",
    "mls_range_compression_30_240",
)

SIGNED_COMMON_SOURCES: frozenset[str] = frozenset(
    name for name in COMMON_SOURCE_FEATURES if name in SIGNED_LOCAL_FEATURES
)


def state_feature_names() -> tuple[str, ...]:
    local: list[str] = [
        "mls_prior_sigma_1m",
        "mls_prior_range_fraction_1m",
        "mls_return_z_1m",
        "mls_range_fraction_1m",
        "mls_body_signed_1m",
        "mls_close_location_signed_1m",
        "mls_activity_ratio_1m",
        "mls_trade_count_ratio_1m",
        "mls_delta_share_1m",
        "mls_delta_activity_ratio_1m",
    ]
    for horizon in HORIZONS:
        prefix = f"mls_{horizon}m"
        local.extend(
            (
                f"{prefix}_return",
                f"mls_return_z_{horizon}m",
                f"mls_path_efficiency_{horizon}m",
                f"mls_positive_fraction_{horizon}m",
                f"mls_negative_fraction_{horizon}m",
                f"mls_turn_rate_{horizon}m",
                f"mls_delta_share_{horizon}m",
                f"mls_activity_ratio_{horizon}m",
                f"mls_impact_efficiency_{horizon}m",
                f"mls_flow_progress_product_{horizon}m",
            ),
        )
    local.extend(
        (
            "mls_volatility_ratio_30_240",
            "mls_range_compression_30_240",
            "mls_activity_ratio_30_240",
            "mls_delta_persistence_15_60",
        ),
    )
    common: list[str] = []
    for source in COMMON_SOURCE_FEATURES:
        suffix = source.removeprefix("mls_")
        common.extend(
            (
                f"mls_common_{suffix}",
                f"mls_dispersion_{suffix}",
                f"mls_residual_{suffix}",
            ),
        )
    for horizon in HORIZONS:
        common.extend(
            (
                f"mls_common_positive_breadth_{horizon}m",
                f"mls_common_negative_breadth_{horizon}m",
            ),
        )
    return tuple(dict.fromkeys(local + common))


STATE_FEATURES = state_feature_names()
SIGNED_STATE_FEATURES: frozenset[str] = frozenset(
    list(SIGNED_LOCAL_FEATURES)
    + [
        f"mls_common_{source.removeprefix('mls_')}"
        for source in SIGNED_COMMON_SOURCES
    ]
    + [
        f"mls_residual_{source.removeprefix('mls_')}"
        for source in SIGNED_COMMON_SOURCES
    ],
)


class SymbolState:
    """Prior-only robust state for one symbol."""

    def __init__(self) -> None:
        self.history: deque[MinuteObservation] = deque(maxlen=HISTORY_BARS)
        self.last_ts_ns: int | None = None

    def _prior_scale(self, attribute: str, floor: float) -> float:
        prior = list(self.history)[-BASELINE_BARS:]
        values = [abs(_finite(getattr(item, attribute))) for item in prior]
        return max(_safe_median(values, floor), floor)

    def observe(self, candle: Any) -> dict[str, float]:
        ts_ns = int(getattr(candle, "ts_close_ns"))
        if self.last_ts_ns is not None and ts_ns <= self.last_ts_ns:
            raise RuntimeError(
                f"non-increasing one-minute state timestamp {ts_ns} <= {self.last_ts_ns}",
            )
        open_price = _finite(getattr(candle, "open"))
        high = _finite(getattr(candle, "high"))
        low = _finite(getattr(candle, "low"))
        close = _finite(getattr(candle, "close"))
        quote = max(0.0, _finite(getattr(candle, "quote_volume", 0.0), 0.0))
        trade_count = max(0.0, _finite(getattr(candle, "trade_count", 0.0), 0.0))
        taker_buy = _finite(getattr(candle, "taker_buy_quote_volume", 0.0), 0.0)
        if not all(math.isfinite(value) and value > 0.0 for value in (open_price, high, low, close)):
            raise ValueError("invalid positive OHLC for causal state")
        previous_close = self.history[-1].close if self.history else close
        log_close = math.log(close)
        log_return = math.log(close / previous_close) if previous_close > 0.0 else 0.0
        signed_quote = 2.0 * taker_buy - quote if quote > 0.0 else 0.0
        delta_share = signed_quote / quote if quote > 0.0 else 0.0
        price_range = max(high - low, close * 1e-12)
        range_fraction = price_range / close
        body_fraction = (close - open_price) / price_range
        close_location_signed = 2.0 * ((close - low) / price_range) - 1.0

        prior_quote = max(
            _safe_median((item.quote_volume for item in self.history), 1e-12),
            1e-12,
        )
        prior_count = max(
            _safe_median((item.trade_count for item in self.history), 1.0),
            1.0,
        )
        prior_abs_delta = max(
            _safe_median((abs(item.signed_taker_quote) for item in self.history), 1e-12),
            1e-12,
        )
        sigma_1m = self._prior_scale("log_return", 1e-8)
        prior_range = max(
            _safe_median((item.range_fraction for item in self.history), 1e-8),
            1e-8,
        )

        current = MinuteObservation(
            ts_close_ns=ts_ns,
            close=close,
            log_close=log_close,
            log_return=log_return,
            quote_volume=quote,
            trade_count=trade_count,
            signed_taker_quote=signed_quote,
            delta_share=delta_share,
            range_fraction=range_fraction,
            body_fraction=body_fraction,
            close_location_signed=close_location_signed,
        )
        combined = list(self.history) + [current]
        output: dict[str, float] = {
            "mls_prior_sigma_1m": sigma_1m,
            "mls_prior_range_fraction_1m": prior_range,
            "mls_return_z_1m": log_return / sigma_1m,
            "mls_range_fraction_1m": range_fraction,
            "mls_body_signed_1m": body_fraction,
            "mls_close_location_signed_1m": close_location_signed,
            "mls_activity_ratio_1m": quote / prior_quote,
            "mls_trade_count_ratio_1m": trade_count / prior_count,
            "mls_delta_share_1m": delta_share,
            "mls_delta_activity_ratio_1m": abs(signed_quote) / prior_abs_delta,
        }

        for horizon in HORIZONS:
            window = combined[-horizon:]
            if len(window) < horizon:
                continue
            returns = [item.log_return for item in window]
            net_return = window[-1].log_close - window[0].log_close + window[0].log_return
            variation = sum(abs(value) for value in returns)
            signs = [1 if value > 0.0 else -1 if value < 0.0 else 0 for value in returns]
            q_sum = sum(item.quote_volume for item in window)
            d_sum = sum(item.signed_taker_quote for item in window)
            return_z = net_return / (sigma_1m * math.sqrt(horizon))
            delta = d_sum / q_sum if q_sum > 0.0 else 0.0
            activity = q_sum / max(prior_quote * horizon, 1e-12)
            impact = return_z / (abs(delta) + 0.05)
            output[f"mls_{horizon}m_return"] = net_return
            output[f"mls_return_z_{horizon}m"] = return_z
            output[f"mls_path_efficiency_{horizon}m"] = (
                net_return / variation if variation > 0.0 else 0.0
            )
            output[f"mls_positive_fraction_{horizon}m"] = sum(v > 0.0 for v in returns) / horizon
            output[f"mls_negative_fraction_{horizon}m"] = sum(v < 0.0 for v in returns) / horizon
            output[f"mls_turn_rate_{horizon}m"] = _turn_rate(signs)
            output[f"mls_delta_share_{horizon}m"] = delta
            output[f"mls_activity_ratio_{horizon}m"] = activity
            output[f"mls_impact_efficiency_{horizon}m"] = impact
            output[f"mls_flow_progress_product_{horizon}m"] = return_z * delta

        def median_abs_return(length: int) -> float:
            values = [abs(item.log_return) for item in combined[-length:]]
            return _safe_median(values, float("nan"))

        def median_range(length: int) -> float:
            values = [item.range_fraction for item in combined[-length:]]
            return _safe_median(values, float("nan"))

        def mean_activity(length: int) -> float:
            values = [item.quote_volume for item in combined[-length:]]
            return sum(values) / max(len(values) * prior_quote, 1e-12) if values else float("nan")

        vol30 = median_abs_return(30)
        vol240 = median_abs_return(240)
        range30 = median_range(30)
        range240 = median_range(240)
        output["mls_volatility_ratio_30_240"] = _safe_ratio(vol30, vol240)
        output["mls_range_compression_30_240"] = _safe_ratio(range30, range240)
        output["mls_activity_ratio_30_240"] = _safe_ratio(mean_activity(30), mean_activity(240))
        delta15 = output.get("mls_delta_share_15m", float("nan"))
        delta60 = output.get("mls_delta_share_60m", float("nan"))
        output["mls_delta_persistence_15_60"] = (
            delta15 * delta60 if math.isfinite(delta15) and math.isfinite(delta60) else float("nan")
        )

        self.history.append(current)
        self.last_ts_ns = ts_ns
        return output


class CausalMarketState:
    """Synchronized multi-symbol state with one completed-minute watermark."""

    def __init__(self, symbols: Sequence[str]) -> None:
        if len(set(symbols)) != len(symbols):
            raise ValueError("symbols must be unique")
        self.symbols = tuple(symbols)
        self.symbol_state = {symbol: SymbolState() for symbol in self.symbols}
        self._pending_ts: int | None = None
        self._pending: dict[str, dict[str, float]] = {}
        self._latest: dict[str, dict[str, float]] = {}
        self.watermark_ns: int | None = None

    def observe(self, symbol: str, candle: Any) -> None:
        if symbol not in self.symbol_state:
            raise KeyError(symbol)
        ts_ns = int(getattr(candle, "ts_close_ns"))
        if self._pending_ts is None:
            self._pending_ts = ts_ns
        if ts_ns != self._pending_ts:
            raise RuntimeError(
                f"state watermark changed before synchronization: {self._pending_ts} -> {ts_ns}",
            )
        if symbol in self._pending:
            raise RuntimeError(f"duplicate state bar {symbol} @ {ts_ns}")
        self._pending[symbol] = self.symbol_state[symbol].observe(candle)

    def finalize(self) -> None:
        if self._pending_ts is None:
            return
        missing = sorted(set(self.symbols) - set(self._pending))
        if missing:
            raise RuntimeError(
                f"cannot finalize causal common state @ {self._pending_ts}; missing {missing}",
            )
        output = {symbol: dict(values) for symbol, values in self._pending.items()}
        for source in COMMON_SOURCE_FEATURES:
            values = [output[symbol].get(source, float("nan")) for symbol in self.symbols]
            finite = [value for value in values if math.isfinite(value)]
            common = _safe_median(finite, float("nan"))
            dispersion = _std(finite)
            suffix = source.removeprefix("mls_")
            for symbol in self.symbols:
                local = output[symbol].get(source, float("nan"))
                output[symbol][f"mls_common_{suffix}"] = common
                output[symbol][f"mls_dispersion_{suffix}"] = dispersion
                output[symbol][f"mls_residual_{suffix}"] = (
                    local - common if math.isfinite(local) and math.isfinite(common) else float("nan")
                )
        for horizon in HORIZONS:
            source = f"mls_return_z_{horizon}m"
            values = [output[symbol].get(source, float("nan")) for symbol in self.symbols]
            finite = [value for value in values if math.isfinite(value)]
            positive = sum(value > 0.0 for value in finite) / len(finite) if finite else float("nan")
            negative = sum(value < 0.0 for value in finite) / len(finite) if finite else float("nan")
            for symbol in self.symbols:
                output[symbol][f"mls_common_positive_breadth_{horizon}m"] = positive
                output[symbol][f"mls_common_negative_breadth_{horizon}m"] = negative
        self._latest = output
        self.watermark_ns = self._pending_ts
        self._pending_ts = None
        self._pending = {}

    def snapshot(self, symbol: str, ts_ns: int | None = None) -> Mapping[str, float]:
        if ts_ns is not None and self.watermark_ns != int(ts_ns):
            raise RuntimeError(
                f"state watermark mismatch for {symbol}: {self.watermark_ns} != {ts_ns}",
            )
        if symbol not in self._latest:
            raise RuntimeError(f"no finalized causal state for {symbol}")
        return self._latest[symbol]

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "policy": STATE_POLICY,
            "symbols": self.symbols,
            "watermark_ns": self.watermark_ns,
            "feature_count": len(STATE_FEATURES),
        }


def _flow_candle_from_row(row: Mapping[str, Any], ts_ns: int) -> Any:
    class RowCandle:
        pass

    candle = RowCandle()
    candle.ts_close_ns = int(ts_ns)
    candle.open = _finite(row.get("open"))
    candle.high = _finite(row.get("high"))
    candle.low = _finite(row.get("low"))
    candle.close = _finite(row.get("close"))
    candle.quote_volume = _finite(row.get("quote_volume", 0.0), 0.0)
    candle.trade_count = int(_finite(row.get("count", row.get("trade_count", 0)), 0.0))
    candle.taker_buy_quote_volume = _finite(row.get("taker_buy_quote_volume", 0.0), 0.0)
    return candle


def build_state_table(frames: Mapping[str, Any]) -> Any:
    """Build an exact offline table using the same incremental state machine."""

    import pandas as pd

    symbols = tuple(frames)
    prepared: dict[str, Any] = {}
    common_index = None
    for symbol, raw in frames.items():
        frame = raw.copy().sort_values("open_time_dt")
        frame["ts"] = pd.DatetimeIndex(frame["open_time_dt"]) + pd.Timedelta(minutes=1)
        frame = frame.set_index("ts", drop=True)
        prepared[symbol] = frame
        common_index = frame.index if common_index is None else common_index.intersection(frame.index)
    if common_index is None:
        return pd.DataFrame()
    common_index = common_index.sort_values()
    state = CausalMarketState(symbols)
    rows: list[dict[str, Any]] = []
    for ts in common_index:
        ts_ns = int(pd.Timestamp(ts).value)
        for symbol in symbols:
            record = prepared[symbol].loc[ts]
            if isinstance(record, pd.DataFrame):
                raise RuntimeError(f"duplicate minute frame key {symbol} @ {ts}")
            state.observe(symbol, _flow_candle_from_row(record, ts_ns))
        state.finalize()
        for symbol in symbols:
            rows.append(
                {
                    "symbol": symbol,
                    "ts": pd.Timestamp(ts),
                    **dict(state.snapshot(symbol, ts_ns)),
                },
            )
    output = pd.DataFrame(rows)
    return output.set_index(["symbol", "ts"]).sort_index()
