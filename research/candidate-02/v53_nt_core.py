"""Causal signal construction for candidate-02 full-auction rotation v53.

This module does not simulate fills, positions, fees, or NAV. It only converts
completed market observations into deterministic trade intents. NautilusTrader
owns every execution and account transition in :mod:`v53_nt_backtest`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import io
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence
import zipfile

import numpy as np
import pandas as pd

NS_MINUTE = 60_000_000_000
UTC = "UTC"
KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_base",
    "taker_buy_quote",
    "ignore",
]


@dataclass(frozen=True, slots=True)
class RotationConfig:
    auction_bars_5m: int = 12
    prior_quantile_days: int = 30
    prior_quantile_min_rows: int = 1000
    volatility_quantile: float = 0.70
    efficiency_quantile: float = 0.45
    vpin_quantile: float = 0.55
    oi_abs_quantile: float = 0.70
    z_min: float = 1.25
    excursion_flow_min: float = 0.0
    confirmation_return_min: float = 0.0
    confirmation_depth_min: float = 0.0
    target_extension: float = 1.0
    stop_lookback_minutes: int = 20
    atr_lookback_minutes: int = 60
    stop_buffer_atr: float = 0.15
    minimum_cost_after_rr: float = 1.0
    maximum_cost_after_rr: float = 3.0
    maximum_holding_minutes: int = 120

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "RotationConfig":
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"unknown v53 config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        if self.auction_bars_5m < 3:
            raise ValueError("auction_bars_5m must be at least 3")
        if self.prior_quantile_days <= 0 or self.prior_quantile_min_rows <= 0:
            raise ValueError("prior quantile history must be positive")
        if self.z_min <= 0 or self.stop_lookback_minutes <= 1:
            raise ValueError("z_min and stop lookback must be positive")
        if self.atr_lookback_minutes < 10:
            raise ValueError("atr lookback is too short")
        if self.stop_buffer_atr < 0:
            raise ValueError("stop buffer cannot be negative")
        if not 0 < self.minimum_cost_after_rr <= self.maximum_cost_after_rr:
            raise ValueError("invalid cost-after reward/risk bounds")
        if self.maximum_holding_minutes <= 0:
            raise ValueError("maximum holding time must be positive")


@dataclass(frozen=True, slots=True)
class CostConfig:
    entry_fee_rate: Decimal
    target_fee_rate: Decimal
    stop_fee_rate: Decimal
    entry_slippage_rate: Decimal
    stop_slippage_rate: Decimal
    market_impact_rate: Decimal
    funding_rate_allowance: Decimal

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "CostConfig":
        return cls(**{name: Decimal(str(values[name])) for name in cls.__dataclass_fields__})

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class RotationSignal:
    scenario_id: str
    observed_time_ns: int
    side: str
    entry_reference: float
    stop_price: float
    target_price: float
    cost_after_reward_risk: float
    score: float
    max_hold_minutes: int
    source_feature_open_time_ns: int
    source_feature_available_time_ns: int
    source_max_market_time_ns: int
    details: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _timestamp_numeric_to_ns(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="raise").astype("int64")
    maximum = int(numeric.max())
    if maximum >= 100_000_000_000_000:
        return numeric * 1_000
    return numeric * 1_000_000


def _read_kline_archive(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if not name.endswith("/")]
        if len(members) != 1:
            raise ValueError(f"expected one CSV in {path}, found {members}")
        raw = archive.read(members[0])
    frame = pd.read_csv(io.BytesIO(raw), header=None)
    if frame.empty:
        raise ValueError(f"empty kline archive: {path}")
    if str(frame.iloc[0, 0]).strip().lower() in {"open_time", "open time"}:
        frame = frame.iloc[1:].reset_index(drop=True)
    if frame.shape[1] < len(KLINE_COLUMNS):
        raise ValueError(f"unexpected kline column count in {path}: {frame.shape[1]}")
    frame = frame.iloc[:, : len(KLINE_COLUMNS)]
    frame.columns = KLINE_COLUMNS
    return frame


def load_raw_one_minute(raw_directory: Path) -> pd.DataFrame:
    archives = sorted(raw_directory.glob("BTCUSDT-1m-*.zip"))
    if not archives:
        raise FileNotFoundError(f"no BTCUSDT one-minute archives in {raw_directory}")
    frames = [_read_kline_archive(path) for path in archives]
    combined = pd.concat(frames, ignore_index=True)
    open_ns = _timestamp_numeric_to_ns(combined["open_time"])
    combined["close_ns"] = open_ns + NS_MINUTE
    for column in ("open", "high", "low", "close", "volume"):
        combined[column] = pd.to_numeric(combined[column], errors="raise").astype("float64")
    combined.sort_values("close_ns", inplace=True)
    if combined["close_ns"].duplicated().any():
        raise ValueError("duplicate one-minute close timestamps")
    index = pd.to_datetime(combined["close_ns"], unit="ns", utc=True)
    result = combined[["open", "high", "low", "close", "volume"]].copy()
    result.index = index
    result.index.name = "close_time_utc"
    if not (
        (result["low"] <= result[["open", "close"]].min(axis=1)).all()
        and (result["high"] >= result[["open", "close"]].max(axis=1)).all()
        and (result["high"] >= result["low"]).all()
        and (result["volume"] >= 0).all()
    ):
        raise ValueError("one-minute OHLCV integrity failure")
    return result


def load_feature_matrix(npz_path: Path, columns_path: Path) -> pd.DataFrame:
    columns = json.loads(columns_path.read_text(encoding="utf-8"))
    archive = np.load(npz_path)
    timestamps = archive["timestamps_ns"].astype(np.int64, copy=False)
    values = archive["values"].astype(np.float64, copy=False)
    if values.ndim != 2 or values.shape[0] != timestamps.shape[0]:
        raise ValueError("feature matrix shape mismatch")
    if values.shape[1] != len(columns):
        raise ValueError("feature column count mismatch")
    median = float(np.nanmedian(timestamps))
    unit = "ms" if median < 1e17 else "ns"
    index = pd.to_datetime(timestamps, unit=unit, utc=True)
    frame = pd.DataFrame(values, index=index, columns=[str(value) for value in columns])
    frame.sort_index(inplace=True)
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("feature timestamps must be strictly increasing")
    required = {
        "close",
        "log_ret_5m",
        "realized_vol_30m",
        "vol_5m",
        "taker_buy_ratio_5m",
        "depth_imbalance_1pct",
        "vpin_50",
        "hawkes_net",
        "oi_change_1h",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"feature columns missing: {missing}")
    allowed = [column for column in frame.columns if not column.startswith("fwd_")]
    return frame[allowed].copy()


def _prior_quantile(
    series: pd.Series,
    *,
    days: int,
    quantile: float,
    minimum_rows: int,
) -> pd.Series:
    rows = days * 288
    return series.shift(1).rolling(rows, min_periods=minimum_rows).quantile(quantile)


def build_state(features: pd.DataFrame, config: RotationConfig) -> pd.DataFrame:
    x = features.copy()
    bars = config.auction_bars_5m
    weight = x["vol_5m"].replace(0.0, np.nan)
    weighted = (x["close"] * weight).rolling(bars, min_periods=bars).sum()
    weight_sum = weight.rolling(bars, min_periods=bars).sum()
    x["vwap60"] = weighted / weight_sum
    x["std60"] = x["close"].rolling(bars, min_periods=bars).std(ddof=0)
    x["zvwap"] = (x["close"] - x["vwap60"]) / x["std60"].replace(0.0, np.nan)
    x["path60"] = x["close"].diff().abs().rolling(bars, min_periods=bars).sum()
    x["eff60"] = (x["close"] - x["close"].shift(bars)).abs() / x["path60"].replace(0.0, np.nan)
    x["effq"] = _prior_quantile(
        x["eff60"],
        days=config.prior_quantile_days,
        quantile=config.efficiency_quantile,
        minimum_rows=config.prior_quantile_min_rows,
    )
    x["vpinq"] = _prior_quantile(
        x["vpin_50"],
        days=config.prior_quantile_days,
        quantile=config.vpin_quantile,
        minimum_rows=config.prior_quantile_min_rows,
    )
    x["abs_oiq"] = _prior_quantile(
        x["oi_change_1h"].abs(),
        days=config.prior_quantile_days,
        quantile=config.oi_abs_quantile,
        minimum_rows=config.prior_quantile_min_rows,
    )
    x["z_prev"] = x["zvwap"].shift(1)
    x["exc_dir"] = np.sign(x["z_prev"])
    x["exc_flow"] = x["exc_dir"] * (2.0 * x["taker_buy_ratio_5m"].shift(1) - 1.0)
    x["confirm_ret"] = -x["exc_dir"] * x["log_ret_5m"]
    x["confirm_depth"] = -x["exc_dir"] * x["depth_imbalance_1pct"]
    x["z_improves"] = x["zvwap"].abs() < x["z_prev"].abs()
    x["feature_available_time"] = x.index + pd.Timedelta(minutes=5)
    return x


def _true_range(raw: pd.DataFrame) -> pd.Series:
    previous_close = raw["close"].shift(1)
    return pd.concat(
        [
            raw["high"] - raw["low"],
            (raw["high"] - previous_close).abs(),
            (raw["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def cost_after_reward_risk(
    *,
    entry: float,
    stop: float,
    target: float,
    side: str,
    costs: CostConfig,
) -> float:
    entry_d = Decimal(str(entry))
    stop_d = Decimal(str(stop))
    target_d = Decimal(str(target))
    risk = abs(entry_d - stop_d)
    risk += entry_d * (
        costs.entry_fee_rate
        + costs.entry_slippage_rate
        + costs.market_impact_rate
        + costs.funding_rate_allowance
    )
    risk += stop_d * (
        costs.stop_fee_rate + costs.stop_slippage_rate + costs.market_impact_rate
    )
    if side == "BUY":
        gross_reward = target_d - entry_d
    elif side == "SELL":
        gross_reward = entry_d - target_d
    else:
        raise ValueError(f"unknown side: {side}")
    reward = gross_reward
    reward -= entry_d * (
        costs.entry_fee_rate
        + costs.entry_slippage_rate
        + costs.market_impact_rate
        + costs.funding_rate_allowance
    )
    reward -= target_d * (costs.target_fee_rate + costs.market_impact_rate)
    if risk <= 0:
        return math.nan
    return float(reward / risk)


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: RotationConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = pd.Timestamp(evaluation_start)
    end = pd.Timestamp(evaluation_end)
    if start.tzinfo is None:
        start = start.tz_localize(UTC)
    else:
        start = start.tz_convert(UTC)
    if end.tzinfo is None:
        end = end.tz_localize(UTC)
    else:
        end = end.tz_convert(UTC)
    if end <= start:
        raise ValueError("evaluation_end must be after evaluation_start")

    x = state.loc[start - pd.Timedelta(days=2) : end].copy()
    balanced = (
        (x["eff60"] <= x["effq"])
        & (x["vpin_50"] <= x["vpinq"])
        & (x["oi_change_1h"].abs() <= x["abs_oiq"])
    )
    excursion = (x["z_prev"].abs() >= config.z_min) & (
        x["exc_flow"] >= config.excursion_flow_min
    )
    confirmation = (
        (x["confirm_ret"] > config.confirmation_return_min)
        & x["z_improves"]
        & (x["confirm_depth"] >= config.confirmation_depth_min)
    )
    candidates = x.loc[balanced & excursion & confirmation]

    tr = _true_range(raw)
    atr = tr.rolling(
        config.atr_lookback_minutes,
        min_periods=max(30, config.atr_lookback_minutes // 2),
    ).median()
    signals: list[RotationSignal] = []
    for feature_open_time, row in candidates.iterrows():
        observed_time = feature_open_time + pd.Timedelta(minutes=5)
        if not start <= observed_time < end:
            continue
        if observed_time not in raw.index:
            continue
        excursion_direction = int(np.sign(row["z_prev"]))
        if excursion_direction == 0:
            continue
        side = "BUY" if excursion_direction < 0 else "SELL"
        entry_reference = float(raw.at[observed_time, "close"])
        center = float(row["vwap60"])
        deviation = abs(float(row["z_prev"])) * float(row["std60"])
        target = center + (-excursion_direction) * config.target_extension * deviation

        history_start = observed_time - pd.Timedelta(minutes=config.stop_lookback_minutes)
        history = raw.loc[(raw.index > history_start) & (raw.index <= observed_time)]
        atr_value = float(atr.asof(observed_time))
        if len(history) < config.stop_lookback_minutes or not math.isfinite(atr_value) or atr_value <= 0:
            continue
        if side == "BUY":
            stop = float(history["low"].min() - config.stop_buffer_atr * atr_value)
            geometry_valid = stop < entry_reference < target
        else:
            stop = float(history["high"].max() + config.stop_buffer_atr * atr_value)
            geometry_valid = target < entry_reference < stop
        if not geometry_valid:
            continue
        rr = cost_after_reward_risk(
            entry=entry_reference,
            stop=stop,
            target=target,
            side=side,
            costs=costs,
        )
        if not math.isfinite(rr) or not (
            config.minimum_cost_after_rr <= rr <= config.maximum_cost_after_rr
        ):
            continue
        observed_ns = int(observed_time.value)
        feature_open_ns = int(feature_open_time.value)
        if observed_ns != feature_open_ns + 5 * NS_MINUTE:
            raise AssertionError("feature availability time mismatch")
        score = abs(float(row["z_prev"])) * (
            1.0 + float(row["confirm_ret"]) / (abs(float(row["log_ret_5m"])) + 1e-12)
        )
        signals.append(
            RotationSignal(
                scenario_id=f"v53-rotation-{observed_ns}",
                observed_time_ns=observed_ns,
                side=side,
                entry_reference=entry_reference,
                stop_price=stop,
                target_price=target,
                cost_after_reward_risk=rr,
                score=score,
                max_hold_minutes=config.maximum_holding_minutes,
                source_feature_open_time_ns=feature_open_ns,
                source_feature_available_time_ns=observed_ns,
                source_max_market_time_ns=observed_ns,
                details={
                    "z_previous": float(row["z_prev"]),
                    "auction_vwap": center,
                    "auction_std": float(row["std60"]),
                    "auction_efficiency": float(row["eff60"]),
                    "efficiency_threshold": float(row["effq"]),
                    "vpin": float(row["vpin_50"]),
                    "vpin_threshold": float(row["vpinq"]),
                    "oi_change_1h": float(row["oi_change_1h"]),
                    "oi_abs_threshold": float(row["abs_oiq"]),
                    "excursion_flow": float(row["exc_flow"]),
                    "confirmation_return": float(row["confirm_ret"]),
                    "confirmation_depth": float(row["confirm_depth"]),
                    "atr_1m": atr_value,
                },
            )
        )
    signals.sort(key=lambda value: value.observed_time_ns)
    if len({item.observed_time_ns for item in signals}) != len(signals):
        raise ValueError("multiple v53 signals share one timestamp")
    for item in signals:
        if item.source_max_market_time_ns > item.observed_time_ns:
            raise AssertionError("future information detected")
    return signals


def signals_to_json(signals: Sequence[RotationSignal]) -> str:
    return json.dumps([item.to_dict() for item in signals], sort_keys=True, separators=(",", ":"))
