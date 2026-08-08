"""Locked informed-inventory state construction for Candidate-02 V155.

This module translates the prospectively locked Candidate-02 V46 market rule
into causal trade intents. It owns no fills, positions, fees, or NAV accounting;
the existing NautilusTrader adapter owns those transitions.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import io
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence
import zipfile

import numpy as np
import pandas as pd

from v53_nt_core import CostConfig, RotationSignal, cost_after_reward_risk

NS_MINUTE = 60_000_000_000
UTC = "UTC"
KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trade_count", "taker_buy_base", "taker_buy_quote", "ignore",
]
METRIC_COLUMNS = (
    "sum_open_interest",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
)
CHANGE_COLUMNS = {
    "oi_change": "sum_open_interest",
    "top_account_change": "count_toptrader_long_short_ratio",
    "top_position_change": "sum_toptrader_long_short_ratio",
    "broad_account_change": "count_long_short_ratio",
    "taker_pressure_change": "sum_taker_long_short_vol_ratio",
}


@dataclass(frozen=True, slots=True)
class InformedInventoryConfig:
    observation_bars: int = 6
    minimum_price_move_atr: float = 0.50
    maximum_price_move_atr: float = 5.00
    atr_history_bars: int = 48
    robust_history_bars: int = 288
    robust_minimum_observations: int = 96
    robust_scale_constant: float = 1.4826
    minimum_oi_z: float = 1.00
    minimum_top_position_directional_z: float = 1.00
    minimum_top_account_directional_z: float = -2.00
    broad_herding_rejection_z: float = 1.00
    minimum_taker_directional_z: float = 0.00
    stop_buffer_atr: float = 0.15
    cost_after_target_r: float = 0.75
    maximum_holding_minutes: int = 300

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "InformedInventoryConfig":
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"unknown informed-inventory config keys: {unknown}")
        return cls(**dict(values))

    def __post_init__(self) -> None:
        if self.observation_bars != 6:
            raise ValueError("the locked rule requires exactly six observation bars")
        if self.atr_history_bars != 48:
            raise ValueError("the locked rule requires exactly 48 prior ATR bars")
        if self.robust_history_bars != 288 or self.robust_minimum_observations != 96:
            raise ValueError("the locked robust-z history is 288 bars with 96 minimum")
        if not math.isclose(self.robust_scale_constant, 1.4826, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("the locked robust-z scale constant is 1.4826")
        if not 0 < self.minimum_price_move_atr < self.maximum_price_move_atr:
            raise ValueError("invalid locked price-move range")
        if self.stop_buffer_atr < 0 or self.cost_after_target_r <= 0:
            raise ValueError("stop buffer and target R must be positive")
        if self.maximum_holding_minutes <= 0:
            raise ValueError("maximum holding time must be positive")


def _timestamp_numeric_to_ns(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="raise").astype("int64")
    return numeric * (1_000 if int(numeric.max()) >= 100_000_000_000_000 else 1_000_000)


def _read_single_csv_archive(path: Path, *, header: int | None) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if not name.endswith("/")]
        if len(members) != 1:
            raise ValueError(f"expected one CSV in {path}, found {members}")
        raw = archive.read(members[0])
    frame = pd.read_csv(io.BytesIO(raw), header=header)
    if frame.empty:
        raise ValueError(f"empty archive: {path}")
    return frame


def _read_kline_archive(path: Path) -> pd.DataFrame:
    frame = _read_single_csv_archive(path, header=None)
    if str(frame.iloc[0, 0]).strip().lower() in {"open_time", "open time"}:
        frame = frame.iloc[1:].reset_index(drop=True)
    if frame.shape[1] < len(KLINE_COLUMNS):
        raise ValueError(f"unexpected kline column count in {path}: {frame.shape[1]}")
    frame = frame.iloc[:, :len(KLINE_COLUMNS)]
    frame.columns = KLINE_COLUMNS
    return frame


def load_raw_one_minute(raw_directory: Path, *, symbol: str = "BTCUSDT") -> pd.DataFrame:
    archives = sorted(raw_directory.glob(f"{symbol}-1m-*.zip"))
    if not archives:
        raise FileNotFoundError(f"no {symbol} one-minute archives in {raw_directory}")
    combined = pd.concat([_read_kline_archive(path) for path in archives], ignore_index=True)
    combined["close_ns"] = _timestamp_numeric_to_ns(combined["open_time"]) + NS_MINUTE
    for column in ("open", "high", "low", "close", "volume", "taker_buy_base"):
        combined[column] = pd.to_numeric(combined[column], errors="raise").astype("float64")
    combined.sort_values("close_ns", inplace=True)
    combined.drop_duplicates("close_ns", keep="last", inplace=True)
    result = combined[["open", "high", "low", "close", "volume", "taker_buy_base"]].copy()
    result.index = pd.to_datetime(combined["close_ns"], unit="ns", utc=True)
    result.index.name = "close_time_utc"
    if result.index.has_duplicates or not result.index.is_monotonic_increasing:
        raise ValueError("one-minute timestamps must be strictly increasing")
    if not (
        (result["low"] <= result[["open", "close"]].min(axis=1)).all()
        and (result["high"] >= result[["open", "close"]].max(axis=1)).all()
        and (result["high"] >= result["low"]).all()
        and (result[["volume", "taker_buy_base"]] >= 0).all().all()
        and (result["taker_buy_base"] <= result["volume"] + 1e-9).all()
    ):
        raise ValueError("one-minute kline integrity failure")
    return result


def load_metrics(metrics_directory: Path, *, symbol: str = "BTCUSDT") -> pd.DataFrame:
    archives = sorted(metrics_directory.glob(f"{symbol}-metrics-*.zip"))
    if not archives:
        raise FileNotFoundError(f"no {symbol} metrics archives in {metrics_directory}")
    frames: list[pd.DataFrame] = []
    for path in archives:
        frame = _read_single_csv_archive(path, header=0)
        missing = sorted({"create_time", *METRIC_COLUMNS} - set(frame.columns))
        if missing:
            raise ValueError(f"metrics columns missing from {path}: {missing}")
        frames.append(frame[["create_time", *METRIC_COLUMNS]].copy())
    combined = pd.concat(frames, ignore_index=True)
    combined["create_time"] = pd.to_datetime(combined["create_time"], utc=True, errors="raise")
    for column in METRIC_COLUMNS:
        combined[column] = pd.to_numeric(combined[column], errors="coerce").astype("float64")
    combined.sort_values("create_time", inplace=True)
    combined.drop_duplicates("create_time", keep="last", inplace=True)
    result = combined.set_index("create_time")
    result.index.name = "observation_time_utc"
    if result.index.has_duplicates or not result.index.is_monotonic_increasing:
        raise ValueError("metrics timestamps must be strictly increasing")
    result = result.mask(result <= 0)
    return result


def aggregate_five_minute(raw: pd.DataFrame) -> pd.DataFrame:
    values = raw.resample("5min", label="right", closed="right", origin="epoch").agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
        "volume": "sum", "taker_buy_base": "sum",
    })
    values["component_minutes"] = raw["close"].resample(
        "5min", label="right", closed="right", origin="epoch",
    ).count()
    values = values.loc[values["component_minutes"] == 5].copy()
    values.index.name = "observation_time_utc"
    return values


def _true_range(frame: pd.DataFrame) -> pd.Series:
    previous_close = frame["close"].shift(1)
    return pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous_close).abs(),
        (frame["low"] - previous_close).abs(),
    ], axis=1).max(axis=1)


def _rolling_mad(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return math.nan
    center = float(np.median(finite))
    return float(np.median(np.abs(finite - center)))


def prior_robust_z(
    series: pd.Series,
    *,
    history_bars: int,
    minimum_observations: int,
    scale_constant: float = 1.4826,
) -> pd.DataFrame:
    """Return a robust-z whose reference contains only prior observations."""
    prior = series.shift(1)
    rolling = prior.rolling(history_bars, min_periods=minimum_observations)
    location = rolling.median()
    mad = rolling.apply(_rolling_mad, raw=True)
    scale = (scale_constant * mad).where(mad > 0)
    return pd.DataFrame({
        "value": series,
        "prior_location": location,
        "prior_mad": mad,
        "prior_scale": scale,
        "robust_z": (series - location) / scale,
    }, index=series.index)


def build_informed_inventory_state(
    *,
    raw_one_minute: pd.DataFrame,
    metrics: pd.DataFrame,
    config: InformedInventoryConfig,
) -> pd.DataFrame:
    state = aggregate_five_minute(raw_one_minute).join(metrics[list(METRIC_COLUMNS)], how="inner")
    if state.empty:
        raise ValueError("no common completed five-minute price/metrics observations")
    lag = config.observation_bars - 1
    state["observation_open"] = state["open"].shift(lag)
    state["price_move"] = state["close"] - state["observation_open"]
    state["price_direction"] = np.sign(state["price_move"])
    state["current_candle_direction"] = np.sign(state["close"] - state["open"])
    state["observation_high"] = state["high"].rolling(config.observation_bars, min_periods=config.observation_bars).max()
    state["observation_low"] = state["low"].rolling(config.observation_bars, min_periods=config.observation_bars).min()
    state["prior_atr"] = _true_range(state).shift(1).rolling(
        config.atr_history_bars, min_periods=config.atr_history_bars,
    ).median()
    state["price_move_atr"] = state["price_move"].abs() / state["prior_atr"].replace(0.0, np.nan)
    for change_name, source_name in CHANGE_COLUMNS.items():
        source = state[source_name].where(state[source_name] > 0)
        state[change_name] = np.log(source / source.shift(lag))
        diagnostic = prior_robust_z(
            state[change_name],
            history_bars=config.robust_history_bars,
            minimum_observations=config.robust_minimum_observations,
            scale_constant=config.robust_scale_constant,
        )
        state[f"{change_name}_prior_location"] = diagnostic["prior_location"]
        state[f"{change_name}_prior_mad"] = diagnostic["prior_mad"]
        state[f"{change_name}_z"] = diagnostic["robust_z"]
    direction = state["price_direction"]
    state["top_position_directional_change"] = direction * state["top_position_change"]
    state["top_position_directional_z"] = direction * state["top_position_change_z"]
    state["top_account_directional_z"] = direction * state["top_account_change_z"]
    state["broad_account_directional_change"] = direction * state["broad_account_change"]
    state["broad_account_directional_z"] = direction * state["broad_account_change_z"]
    state["taker_directional_change"] = direction * state["taker_pressure_change"]
    state["taker_directional_z"] = direction * state["taker_pressure_change_z"]
    state["broad_herding_rejected"] = (
        (state["broad_account_directional_change"] > 0.0)
        & (state["broad_account_directional_z"] >= config.broad_herding_rejection_z)
    )
    return state


def informed_inventory_candidate_mask(
    state: pd.DataFrame,
    config: InformedInventoryConfig,
) -> pd.Series:
    required = {
        "price_direction", "current_candle_direction", "price_move_atr",
        "oi_change", "oi_change_z", "top_position_directional_change",
        "top_position_directional_z", "top_account_directional_z",
        "taker_directional_change", "taker_directional_z", "broad_herding_rejected",
    }
    missing = sorted(required - set(state.columns))
    if missing:
        raise ValueError(f"state columns missing: {missing}")
    direction = state["price_direction"]
    return (
        direction.isin((-1.0, 1.0))
        & (state["price_move_atr"] >= config.minimum_price_move_atr)
        & (state["price_move_atr"] <= config.maximum_price_move_atr)
        & (state["oi_change"] > 0.0)
        & (state["oi_change_z"] >= config.minimum_oi_z)
        & (state["top_position_directional_change"] > 0.0)
        & (state["top_position_directional_z"] >= config.minimum_top_position_directional_z)
        & (state["top_account_directional_z"] >= config.minimum_top_account_directional_z)
        & (~state["broad_herding_rejected"].fillna(True))
        & (state["taker_directional_change"] > 0.0)
        & (state["taker_directional_z"] >= config.minimum_taker_directional_z)
        & (state["current_candle_direction"] == direction)
    ).fillna(False)


def solve_cost_after_target(
    *, entry: float, stop: float, side: str, target_r: float, costs: CostConfig,
) -> float:
    entry_d, stop_d, target_r_d = Decimal(str(entry)), Decimal(str(stop)), Decimal(str(target_r))
    entry_rate = costs.entry_fee_rate + costs.entry_slippage_rate + costs.market_impact_rate + costs.funding_rate_allowance
    stop_rate = costs.stop_fee_rate + costs.stop_slippage_rate + costs.market_impact_rate
    target_rate = costs.target_fee_rate + costs.market_impact_rate
    risk = abs(entry_d - stop_d) + entry_d * entry_rate + stop_d * stop_rate
    if risk <= 0:
        raise ValueError("non-positive expected loss")
    if side == "BUY":
        target = (target_r_d * risk + entry_d * (Decimal("1") + entry_rate)) / (Decimal("1") - target_rate)
    elif side == "SELL":
        target = (entry_d * (Decimal("1") - entry_rate) - target_r_d * risk) / (Decimal("1") + target_rate)
    else:
        raise ValueError(f"unknown side: {side}")
    value = float(target)
    realized_r = cost_after_reward_risk(entry=entry, stop=stop, target=value, side=side, costs=costs)
    if value <= 0 or not math.isclose(realized_r, target_r, rel_tol=1e-10, abs_tol=1e-10):
        raise AssertionError((value, realized_r, target_r))
    return value


def _utc_timestamp(value: pd.Timestamp | str) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize(UTC) if stamp.tzinfo is None else stamp.tz_convert(UTC)


def build_informed_inventory_signals(
    *,
    state: pd.DataFrame,
    raw_one_minute: pd.DataFrame,
    evaluation_start: pd.Timestamp | str,
    evaluation_end: pd.Timestamp | str,
    config: InformedInventoryConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start, end = _utc_timestamp(evaluation_start), _utc_timestamp(evaluation_end)
    if end <= start:
        raise ValueError("evaluation_end must be after evaluation_start")
    mask = informed_inventory_candidate_mask(state, config)
    candidates = state.loc[mask & (state.index >= start) & (state.index < end)]
    signals: list[RotationSignal] = []
    for observed_time, row in candidates.iterrows():
        if observed_time not in raw_one_minute.index:
            continue
        direction = int(row["price_direction"])
        side = "BUY" if direction > 0 else "SELL"
        entry = float(raw_one_minute.at[observed_time, "close"])
        atr = float(row["prior_atr"])
        if not math.isfinite(atr) or atr <= 0:
            continue
        stop = (
            float(row["observation_low"] - config.stop_buffer_atr * atr)
            if side == "BUY"
            else float(row["observation_high"] + config.stop_buffer_atr * atr)
        )
        if stop <= 0 or not (stop < entry if side == "BUY" else entry < stop):
            continue
        target = solve_cost_after_target(
            entry=entry, stop=stop, side=side,
            target_r=config.cost_after_target_r, costs=costs,
        )
        if not (stop < entry < target if side == "BUY" else target < entry < stop):
            continue
        rr = cost_after_reward_risk(entry=entry, stop=stop, target=target, side=side, costs=costs)
        observed_ns = int(observed_time.value)
        signals.append(RotationSignal(
            scenario_id=f"v155-informed-inventory-{observed_ns}",
            observed_time_ns=observed_ns,
            side=side,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            cost_after_reward_risk=rr,
            score=1.0,
            max_hold_minutes=config.maximum_holding_minutes,
            source_feature_open_time_ns=observed_ns - 5 * NS_MINUTE,
            source_feature_available_time_ns=observed_ns,
            source_max_market_time_ns=observed_ns,
            details={
                "module": "INFORMED_INVENTORY_BUILDUP_CONTINUATION",
                "lineage": "candidate-02-v46-locked-rule",
                "observation_bars": config.observation_bars,
                "price_direction": direction,
                "price_move": float(row["price_move"]),
                "price_move_atr": float(row["price_move_atr"]),
                "prior_atr": atr,
                "observation_high": float(row["observation_high"]),
                "observation_low": float(row["observation_low"]),
                "oi_change": float(row["oi_change"]),
                "oi_change_z": float(row["oi_change_z"]),
                "top_position_change": float(row["top_position_change"]),
                "top_position_directional_z": float(row["top_position_directional_z"]),
                "top_account_change": float(row["top_account_change"]),
                "top_account_directional_z": float(row["top_account_directional_z"]),
                "broad_account_change": float(row["broad_account_change"]),
                "broad_account_directional_z": float(row["broad_account_directional_z"]),
                "taker_pressure_change": float(row["taker_pressure_change"]),
                "taker_directional_z": float(row["taker_directional_z"]),
                "broad_herding_rejected": False,
                "target_r_locked": config.cost_after_target_r,
                "score_affects_risk": False,
                "future_information_used": False,
            },
        ))
    signals.sort(key=lambda item: item.observed_time_ns)
    if len({item.observed_time_ns for item in signals}) != len(signals):
        raise ValueError("multiple informed-inventory signals share one timestamp")
    if any(item.source_max_market_time_ns > item.observed_time_ns for item in signals):
        raise AssertionError("future information detected")
    return signals


def state_funnel(state: pd.DataFrame, config: InformedInventoryConfig) -> dict[str, int]:
    direction = state["price_direction"]
    gates = [
        ("direction_known", direction.isin((-1.0, 1.0))),
        ("price_move_in_locked_range", (state["price_move_atr"] >= config.minimum_price_move_atr) & (state["price_move_atr"] <= config.maximum_price_move_atr)),
        ("open_interest_new_inventory", (state["oi_change"] > 0.0) & (state["oi_change_z"] >= config.minimum_oi_z)),
        ("top_position_aligned", (state["top_position_directional_change"] > 0.0) & (state["top_position_directional_z"] >= config.minimum_top_position_directional_z)),
        ("top_account_not_extreme_contradiction", state["top_account_directional_z"] >= config.minimum_top_account_directional_z),
        ("not_broad_herding", ~state["broad_herding_rejected"].fillna(True)),
        ("taker_pressure_aligned", (state["taker_directional_change"] > 0.0) & (state["taker_directional_z"] >= config.minimum_taker_directional_z)),
        ("current_candle_confirms", state["current_candle_direction"] == direction),
    ]
    cumulative = pd.Series(True, index=state.index)
    counts = {"observations": int(len(state))}
    for name, gate in gates:
        cumulative &= gate.fillna(False)
        counts[name] = int(cumulative.sum())
    return counts


def signals_to_json(signals: Sequence[RotationSignal]) -> str:
    return json.dumps([item.to_dict() for item in signals], sort_keys=True, separators=(",", ":"))
