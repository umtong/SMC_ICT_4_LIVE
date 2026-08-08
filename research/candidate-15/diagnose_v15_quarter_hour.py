#!/usr/bin/env python3
"""Candidate 15 V15 causal quarter-hour public-state delivery diagnostic.

This is an alpha-mechanism diagnostic, not a backtest/account engine. It uses
only completed Binance one-minute bars, prior-only walk-forward estimation and
one global non-overlap episode router. NautilusTrader remains mandatory if a
route survives this economic screen.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import time
from typing import Any, Iterable
from urllib.request import Request, urlopen
from zipfile import ZipFile

import numpy as np
import pandas as pd

COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
)
PUBLIC_FEATURES = (
    "ret_15m", "ret_60m", "ret_240m", "ret_720m", "ret_1440m",
    "median_ret_15m", "median_ret_60m", "median_ret_240m",
    "median_ret_720m", "median_ret_1440m",
    "relative_ret_15m", "relative_ret_60m", "relative_ret_240m",
    "relative_ret_720m", "relative_ret_1440m",
    "rv_60m", "rv_240m", "rv_1440m", "range_position_240m",
    "range_position_1440m", "volume_ratio_15m_4h", "volume_ratio_60m_24h",
    "trend_60_240", "trend_240_1440", "breadth_60m", "breadth_240m",
    "tod_sin", "tod_cos", "symbol_BTC", "symbol_ETH", "symbol_SOL", "symbol_XRP",
)
ROUTE_REVERSION = "QH_INVENTORY_REVERSION_30M"
ROUTE_DELIVERY = "QH_PUBLIC_DELIVERY_8H"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def month_starts(start: date, end_exclusive: date) -> Iterable[date]:
    cursor = date(start.year, start.month, 1)
    while cursor < end_exclusive:
        yield cursor
        cursor = date(
            cursor.year + int(cursor.month == 12),
            1 if cursor.month == 12 else cursor.month + 1,
            1,
        )


def download(url: str, destination: Path, retries: int = 5) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size > 100:
        try:
            with ZipFile(destination) as archive:
                if archive.testzip() is None:
                    return
        except Exception:
            destination.unlink(missing_ok=True)
    request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-15-v15"})
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=90) as response:  # noqa: S310 fixed host
                payload = response.read()
            if len(payload) < 100:
                raise RuntimeError(f"small response from {url}")
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_bytes(payload)
            with ZipFile(temporary) as archive:
                bad = archive.testzip()
                if bad is not None:
                    raise RuntimeError(f"corrupt member {bad}")
            temporary.replace(destination)
            return
        except Exception as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"download failed {url}: {last}")


def read_archive(path: Path) -> pd.DataFrame:
    with ZipFile(path) as archive:
        members = [name for name in archive.namelist() if not name.endswith("/")]
        if len(members) != 1:
            raise RuntimeError(f"unexpected members in {path}: {members}")
        payload = archive.read(members[0])
    frame = pd.read_csv(BytesIO(payload))
    if not set(COLUMNS).issubset(frame.columns):
        frame = pd.read_csv(BytesIO(payload), header=None, names=COLUMNS)
    else:
        frame = frame.loc[:, COLUMNS]
    return frame[pd.to_numeric(frame["open_time"], errors="coerce").notna()].copy()


def load_symbol(
    symbol: str,
    start: date,
    end_exclusive: date,
    data_dir: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    manifest: list[dict[str, Any]] = []
    for month in month_starts(start, end_exclusive):
        token = f"{month.year:04d}-{month.month:02d}"
        filename = f"{symbol}-1m-{token}.zip"
        url = (
            "https://data.binance.vision/data/futures/um/monthly/klines/"
            f"{symbol}/1m/{filename}"
        )
        path = data_dir / symbol / filename
        download(url, path)
        frame = read_archive(path)
        frames.append(frame)
        manifest.append(
            {
                "symbol": symbol,
                "month": token,
                "url": url,
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256(path.read_bytes()).hexdigest(),
                "rows": len(frame.index),
            }
        )
    raw = pd.concat(frames, ignore_index=True)
    raw = raw.drop_duplicates(subset=["open_time"], keep="last")
    raw = raw.sort_values("open_time", kind="stable")
    numeric_open = pd.to_numeric(raw["open_time"], errors="raise").astype("int64")
    first = int(numeric_open.iloc[0])
    if 1_000_000_000_000 <= first < 10_000_000_000_000:
        unit = "ms"
    elif 1_000_000_000_000_000 <= first < 10_000_000_000_000_000:
        unit = "us"
    else:
        raise RuntimeError(f"unsupported timestamp magnitude {first}")
    open_index = pd.to_datetime(numeric_open, unit=unit, utc=True)
    result = pd.DataFrame(index=open_index + pd.Timedelta(minutes=1))
    result["open_time"] = open_index.to_numpy()
    for name in (
        "open", "high", "low", "close", "volume", "quote_volume", "trades",
        "taker_buy_volume", "taker_buy_quote_volume",
    ):
        result[name] = pd.to_numeric(raw[name], errors="raise").to_numpy(copy=True)
    result = result[~result.index.duplicated(keep="last")].sort_index()
    lower = pd.Timestamp(start, tz="UTC")
    upper = pd.Timestamp(end_exclusive, tz="UTC")
    result = result[(result.index > lower) & (result.index <= upper)]
    expected_minutes = int((upper - lower).total_seconds() // 60)
    coverage = len(result.index) / max(expected_minutes, 1)
    if coverage < 0.995:
        raise RuntimeError(f"insufficient {symbol} minute coverage: {coverage:.6f}")
    return result, manifest


def safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0.0, np.nan)


def own_event_features(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    close = frame["close"].astype(float)
    pre = close.shift(1)
    minute_ret = pre.pct_change()
    open_stamp = pd.DatetimeIndex(frame["open_time"])
    event_mask = open_stamp.minute.isin((0, 15, 30, 45)) & (open_stamp.second == 0)
    event_index = frame.index[event_mask]
    out = pd.DataFrame(index=event_index)
    out["symbol"] = symbol
    out["event_open_time"] = open_stamp[event_mask].to_numpy()
    out["entry_price"] = close[event_mask].to_numpy()
    volume = frame["volume"].astype(float)
    taker = frame["taker_buy_volume"].astype(float)
    opening_flow = (2.0 * safe_div(taker, volume) - 1.0).clip(-1.0, 1.0)
    out["opening_flow"] = opening_flow[event_mask].to_numpy()
    out["opening_abs_flow"] = out["opening_flow"].abs()
    for minutes in (15, 60, 240, 720, 1440):
        series = pre / pre.shift(minutes) - 1.0
        out[f"ret_{minutes}m"] = series[event_mask].to_numpy()
    out["rv_60m"] = (
        minute_ret.rolling(60, min_periods=60).std(ddof=0) * math.sqrt(60)
    )[event_mask].to_numpy()
    out["rv_240m"] = (
        minute_ret.rolling(240, min_periods=240).std(ddof=0) * math.sqrt(240)
    )[event_mask].to_numpy()
    out["rv_1440m"] = (
        minute_ret.rolling(1440, min_periods=1440).std(ddof=0) * math.sqrt(1440)
    )[event_mask].to_numpy()
    low_240 = frame["low"].shift(1).rolling(240, min_periods=240).min()
    high_240 = frame["high"].shift(1).rolling(240, min_periods=240).max()
    low_1440 = frame["low"].shift(1).rolling(1440, min_periods=1440).min()
    high_1440 = frame["high"].shift(1).rolling(1440, min_periods=1440).max()
    out["range_position_240m"] = (
        safe_div(pre - low_240, high_240 - low_240) - 0.5
    )[event_mask].to_numpy()
    out["range_position_1440m"] = (
        safe_div(pre - low_1440, high_1440 - low_1440) - 0.5
    )[event_mask].to_numpy()
    vol15 = volume.shift(1).rolling(15, min_periods=15).sum()
    vol60 = volume.shift(1).rolling(60, min_periods=60).sum()
    ratio15 = safe_div(
        vol15,
        volume.shift(1).rolling(240, min_periods=240).sum() / 16.0,
    ) - 1.0
    ratio60 = safe_div(
        vol60,
        volume.shift(1).rolling(1440, min_periods=1440).sum() / 24.0,
    ) - 1.0
    out["volume_ratio_15m_4h"] = ratio15[event_mask].to_numpy()
    out["volume_ratio_60m_24h"] = ratio60[event_mask].to_numpy()
    sma60 = pre.rolling(60, min_periods=60).mean()
    sma240 = pre.rolling(240, min_periods=240).mean()
    sma1440 = pre.rolling(1440, min_periods=1440).mean()
    out["trend_60_240"] = (sma60 / sma240 - 1.0)[event_mask].to_numpy()
    out["trend_240_1440"] = (sma240 / sma1440 - 1.0)[event_mask].to_numpy()
    out["target_30m"] = (close.shift(-30) / close - 1.0)[event_mask].to_numpy()
    out["target_480m"] = (close.shift(-480) / close - 1.0)[event_mask].to_numpy()
    out["target_720m"] = (close.shift(-720) / close - 1.0)[event_mask].to_numpy()
    minutes = open_stamp[event_mask].hour * 60 + open_stamp[event_mask].minute
    phase = 2.0 * np.pi * minutes / 1440.0
    out["tod_sin"] = np.sin(phase)
    out["tod_cos"] = np.cos(phase)
    for code in ("BTC", "ETH", "SOL", "XRP"):
        out[f"symbol_{code}"] = float(symbol.startswith(code))
    return out


def build_event_table(
    own: list[pd.DataFrame],
    protocol: dict[str, Any],
) -> pd.DataFrame:
    table = pd.concat(own).sort_index(kind="stable")
    for minutes in (15, 60, 240, 720, 1440):
        name = f"ret_{minutes}m"
        median = table.groupby(level=0)[name].transform("median")
        table[f"median_ret_{minutes}m"] = median
        table[f"relative_ret_{minutes}m"] = table[name] - median
    table["breadth_60m"] = table.groupby(level=0)["ret_60m"].transform(
        lambda values: np.sign(values).mean(),
    )
    table["breadth_240m"] = table.groupby(level=0)["ret_240m"].transform(
        lambda values: np.sign(values).mean(),
    )
    excluded_hours = set(
        int(value)
        for value in protocol["data"]["exclude_funding_settlement_utc_hours"]
    )
    open_stamp = pd.DatetimeIndex(table["event_open_time"])
    funding_mask = (open_stamp.minute == 0) & open_stamp.hour.isin(excluded_hours)
    table["funding_settlement_excluded"] = funding_mask
    table["target_mature_30m"] = table.index + pd.Timedelta(minutes=30)
    table["target_mature_480m"] = table.index + pd.Timedelta(minutes=480)
    table["target_mature_720m"] = table.index + pd.Timedelta(minutes=720)
    required = list(PUBLIC_FEATURES) + [
        "opening_flow", "opening_abs_flow", "target_30m", "target_480m",
        "target_720m",
    ]
    table = table.replace([np.inf, -np.inf], np.nan).dropna(subset=required)
    return table.sort_index(kind="stable")


@dataclass(slots=True)
class RidgeModel:
    mean: np.ndarray
    scale: np.ndarray
    beta: np.ndarray

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        values = frame.loc[:, PUBLIC_FEATURES].to_numpy(dtype=float)
        standardized = (values - self.mean) / self.scale
        design = np.column_stack((np.ones(len(standardized)), standardized))
        return design @ self.beta


def fit_ridge(frame: pd.DataFrame, target: str, alpha: float) -> RidgeModel:
    values = frame.loc[:, PUBLIC_FEATURES].to_numpy(dtype=float)
    y = frame[target].to_numpy(dtype=float)
    mean = values.mean(axis=0)
    scale = values.std(axis=0, ddof=0)
    scale[~np.isfinite(scale) | (scale < 1e-12)] = 1.0
    standardized = (values - mean) / scale
    design = np.column_stack((np.ones(len(standardized)), standardized))
    penalty = np.eye(design.shape[1]) * float(alpha)
    penalty[0, 0] = 0.0
    beta = np.linalg.solve(design.T @ design + penalty, design.T @ y)
    return RidgeModel(mean=mean, scale=scale, beta=beta)


def route_cost(protocol: dict[str, Any], route: str) -> float:
    config = protocol["routes"][route]
    return (
        float(config["round_trip_cost_bps"])
        + float(config["funding_reserve_bps"])
    ) / 10_000.0


def signed_outcome(
    frame: pd.DataFrame,
    target: str,
    direction: np.ndarray,
    cost: float,
) -> np.ndarray:
    return direction * frame[target].to_numpy(dtype=float) - cost


def calibration_route(
    frame: pd.DataFrame,
    *,
    route: str,
    predictions: dict[str, np.ndarray],
    protocol: dict[str, Any],
) -> dict[str, Any]:
    walk = protocol["walk_forward"]
    route_config = protocol["routes"][route]
    flow = frame["opening_flow"].to_numpy(dtype=float)
    abs_flow = np.abs(flow)
    flow_threshold = float(
        np.quantile(abs_flow, float(route_config["opening_flow_quantile"]))
    )
    if route == ROUTE_REVERSION:
        primary = predictions["p30"]
        condition = (primary * flow < 0.0) & (abs_flow >= flow_threshold)
        direction = np.sign(primary)
        score = np.abs(primary)
        target = "target_30m"
    elif route == ROUTE_DELIVERY:
        primary = predictions["p480"]
        confirm = predictions["p720"]
        condition = (
            (primary * confirm > 0.0)
            & (primary * flow > 0.0)
            & (abs_flow >= flow_threshold)
        )
        direction = np.sign(primary)
        score = np.minimum(
            np.abs(primary),
            np.abs(confirm) * (480.0 / 720.0),
        )
        target = "target_480m"
    else:
        raise ValueError(route)
    candidates = np.flatnonzero(
        condition & np.isfinite(score) & (direction != 0.0)
    )
    minimum = int(walk["minimum_calibration_candidates"])
    if len(candidates) < minimum:
        return {
            "route": route,
            "active": False,
            "reason": "INSUFFICIENT_CALIBRATION_CANDIDATES",
            "candidate_count": int(len(candidates)),
            "minimum_candidate_count": minimum,
            "flow_threshold": flow_threshold,
        }
    threshold = float(
        np.quantile(score[candidates], float(walk["route_score_quantile"]))
    )
    selected = candidates[score[candidates] >= threshold]
    net = signed_outcome(
        frame.iloc[selected],
        target,
        direction[selected],
        route_cost(protocol, route),
    )
    mean_net = float(net.mean())
    win_rate = float((net > 0.0).mean())
    active = (
        mean_net * 10_000.0 > float(walk["minimum_calibration_mean_net_bps"])
        and win_rate >= float(walk["minimum_calibration_win_rate"])
    )
    return {
        "route": route,
        "active": bool(active),
        "reason": "ACTIVE" if active else "NONPOSITIVE_PRIOR_CALIBRATION",
        "candidate_count": int(len(candidates)),
        "selected_count": int(len(selected)),
        "score_threshold": threshold,
        "flow_threshold": flow_threshold,
        "mean_net_bps": mean_net * 10_000.0,
        "win_rate": win_rate,
        "expected_net": max(mean_net, 0.0),
    }


def score_month(
    frame: pd.DataFrame,
    models: dict[str, RidgeModel],
    calibrations: dict[str, dict[str, Any]],
    protocol: dict[str, Any],
) -> list[dict[str, Any]]:
    predictions = {
        "p30": models["p30"].predict(frame),
        "p480": models["p480"].predict(frame),
        "p720": models["p720"].predict(frame),
    }
    rows: list[dict[str, Any]] = []
    flow = frame["opening_flow"].to_numpy(dtype=float)
    abs_flow = np.abs(flow)
    symbols = frame["symbol"].astype(str).to_numpy()
    timestamps = frame.index.to_numpy()
    for route, calibration in calibrations.items():
        if not calibration.get("active"):
            continue
        threshold = float(calibration["score_threshold"])
        flow_threshold = float(calibration["flow_threshold"])
        if route == ROUTE_REVERSION:
            primary = predictions["p30"]
            condition = (primary * flow < 0.0) & (abs_flow >= flow_threshold)
            direction = np.sign(primary)
            score = np.abs(primary)
            target_name = "target_30m"
            horizon = 30
        else:
            primary = predictions["p480"]
            confirm = predictions["p720"]
            condition = (
                (primary * confirm > 0.0)
                & (primary * flow > 0.0)
                & (abs_flow >= flow_threshold)
            )
            direction = np.sign(primary)
            score = np.minimum(
                np.abs(primary),
                np.abs(confirm) * (480.0 / 720.0),
            )
            target_name = "target_480m"
            horizon = 480
        eligible = np.flatnonzero(
            condition & (score >= threshold) & (direction != 0.0)
        )
        for index in eligible:
            target = float(frame.iloc[index][target_name])
            gross = float(direction[index] * target)
            cost = route_cost(protocol, route)
            strength = float(score[index] / max(threshold, 1e-12))
            rows.append(
                {
                    "event_ts": pd.Timestamp(timestamps[index]).isoformat(),
                    "symbol": symbols[index],
                    "route": route,
                    "direction": "LONG" if direction[index] > 0 else "SHORT",
                    "horizon_minutes": horizon,
                    "score": float(score[index]),
                    "score_threshold": threshold,
                    "opening_flow": float(flow[index]),
                    "calibration_mean_net_bps": float(
                        calibration["mean_net_bps"]
                    ),
                    "rank_value": float(
                        calibration["expected_net"] * min(strength, 4.0)
                    ),
                    "gross_return": gross,
                    "cost_return": cost,
                    "net_return": gross - cost,
                }
            )
    return rows


def walk_forward_candidates(
    table: pd.DataFrame,
    protocol: dict[str, Any],
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    walk = protocol["walk_forward"]
    first = date.fromisoformat(walk["development_start"])
    end = date.fromisoformat(walk["evaluation_end_exclusive"])
    diagnostics: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for month in month_starts(first, end):
        month_start = pd.Timestamp(month, tz="UTC")
        next_month = date(
            month.year + int(month.month == 12),
            1 if month.month == 12 else month.month + 1,
            1,
        )
        month_end = pd.Timestamp(next_month, tz="UTC")
        calibration_start = month_start - pd.Timedelta(
            days=int(walk["calibration_days"])
        )
        fit_start = calibration_start - pd.Timedelta(
            days=int(walk["fit_lookback_days"])
        )
        fit = table[
            (table.index >= fit_start)
            & (table["target_mature_720m"] < calibration_start)
        ]
        calibration = table[
            (table.index >= calibration_start)
            & (table["target_mature_720m"] < month_start)
        ]
        current = table[
            (table.index >= month_start) & (table.index < month_end)
        ]
        fit = fit[~fit["funding_settlement_excluded"]]
        calibration = calibration[~calibration["funding_settlement_excluded"]]
        current = current[~current["funding_settlement_excluded"]]
        if len(fit.index) < int(walk["minimum_fit_rows"]):
            diagnostics.append(
                {
                    "month": month.isoformat(),
                    "classification": "INSUFFICIENT_FIT_ROWS",
                    "fit_rows": len(fit.index),
                    "calibration_rows": len(calibration.index),
                    "current_rows": len(current.index),
                }
            )
            continue
        models = {
            "p30": fit_ridge(
                fit,
                "target_30m",
                float(walk["ridge_alpha"]),
            ),
            "p480": fit_ridge(
                fit,
                "target_480m",
                float(walk["ridge_alpha"]),
            ),
            "p720": fit_ridge(
                fit,
                "target_720m",
                float(walk["ridge_alpha"]),
            ),
        }
        calibration_predictions = {
            name: model.predict(calibration) for name, model in models.items()
        }
        routes = {
            route: calibration_route(
                calibration,
                route=route,
                predictions=calibration_predictions,
                protocol=protocol,
            )
            for route in (ROUTE_REVERSION, ROUTE_DELIVERY)
        }
        diagnostics.append(
            {
                "month": month.isoformat(),
                "classification": "MODEL_FIT",
                "fit_start": fit_start.isoformat(),
                "calibration_start": calibration_start.isoformat(),
                "fit_rows": len(fit.index),
                "calibration_rows": len(calibration.index),
                "current_rows": len(current.index),
                "routes": routes,
            }
        )
        candidates.extend(score_month(current, models, routes, protocol))
    frame = pd.DataFrame(candidates)
    columns = (
        "event_ts", "symbol", "route", "direction", "horizon_minutes",
        "score", "score_threshold", "opening_flow",
        "calibration_mean_net_bps", "rank_value", "gross_return",
        "cost_return", "net_return",
    )
    if frame.empty:
        frame = pd.DataFrame(columns=columns)
    else:
        frame["event_ts"] = pd.to_datetime(frame["event_ts"], utc=True)
        frame = frame.sort_values(
            ["event_ts", "rank_value", "score", "symbol"],
            ascending=[True, False, False, True],
            kind="stable",
        )
    return frame, diagnostics


def arbitrate(candidates: pd.DataFrame) -> tuple[pd.DataFrame, Counter[str]]:
    if candidates.empty:
        return candidates.copy(), Counter()
    selected: list[pd.Series] = []
    skips: Counter[str] = Counter()
    free_at = pd.Timestamp.min.tz_localize("UTC")
    for timestamp, episode in candidates.groupby("event_ts", sort=True):
        winner = episode.iloc[0]
        skips["SAME_EVENT_LOSER"] += max(0, len(episode.index) - 1)
        if timestamp < free_at:
            skips["GLOBAL_POSITION_OCCUPIED"] += 1
            continue
        selected.append(winner)
        free_at = timestamp + pd.Timedelta(
            minutes=int(winner["horizon_minutes"])
        )
    if not selected:
        return candidates.iloc[0:0].copy(), skips
    return pd.DataFrame(selected).reset_index(drop=True), skips


def t_stat(values: pd.Series) -> float | None:
    count = len(values.index)
    if count < 2:
        return None
    standard = float(values.std(ddof=1))
    if not math.isfinite(standard) or standard <= 0.0:
        return None
    return float(values.mean() / (standard / math.sqrt(count)))


def summarize(
    frame: pd.DataFrame,
    start: str,
    end_exclusive: str,
) -> dict[str, Any]:
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end_exclusive, tz="UTC")
    subset = frame[
        (frame["event_ts"] >= start_ts) & (frame["event_ts"] < end_ts)
    ].copy()
    days = int((end_ts - start_ts).total_seconds() // 86_400)
    if subset.empty:
        return {
            "start": start,
            "end_exclusive": end_exclusive,
            "calendar_days": days,
            "trades": 0,
            "trades_per_day": 0.0,
            "mean_gross_bps": None,
            "mean_net_bps": None,
            "net_t_stat": None,
            "win_rate_after_cost": None,
            "payoff_ratio": None,
            "positive_months": 0,
            "active_months": 0,
            "positive_month_share": 0.0,
            "route_counts": {},
            "symbol_counts": {},
            "route_stats": {},
        }
    wins = subset[subset["net_return"] > 0.0]["net_return"]
    losses = subset[subset["net_return"] < 0.0]["net_return"]
    payoff = None
    if len(wins.index) and len(losses.index):
        payoff = float(wins.mean() / abs(losses.mean()))
    month_net = (
        subset.set_index("event_ts")["net_return"].resample("MS").mean().dropna()
    )
    route_stats: dict[str, Any] = {}
    for route, routed in subset.groupby("route"):
        route_stats[str(route)] = {
            "trades": len(routed.index),
            "mean_net_bps": float(routed["net_return"].mean() * 10_000.0),
            "win_rate_after_cost": float(
                (routed["net_return"] > 0.0).mean()
            ),
            "net_t_stat": t_stat(routed["net_return"]),
        }
    return {
        "start": start,
        "end_exclusive": end_exclusive,
        "calendar_days": days,
        "trades": len(subset.index),
        "trades_per_day": len(subset.index) / max(days, 1),
        "mean_gross_bps": float(subset["gross_return"].mean() * 10_000.0),
        "mean_net_bps": float(subset["net_return"].mean() * 10_000.0),
        "net_t_stat": t_stat(subset["net_return"]),
        "win_rate_after_cost": float((subset["net_return"] > 0.0).mean()),
        "payoff_ratio": payoff,
        "positive_months": int((month_net > 0.0).sum()),
        "active_months": len(month_net.index),
        "positive_month_share": (
            float((month_net > 0.0).mean()) if len(month_net.index) else 0.0
        ),
        "route_counts": dict(Counter(subset["route"].astype(str))),
        "symbol_counts": dict(Counter(subset["symbol"].astype(str))),
        "route_stats": route_stats,
    }


def concentration_pass(summary: dict[str, Any]) -> bool:
    counts = summary.get("symbol_counts") or {}
    total = int(summary.get("trades") or 0)
    if not total or not counts:
        return False
    return max(counts.values()) / total <= 0.75


def render_result(payload: dict[str, Any]) -> str:
    development = payload["development"]
    evaluation = payload["evaluation"]
    checks = payload["advance_checks"]
    lines = [
        "# Candidate 15 V15 — Quarter-hour public-state delivery diagnostic",
        "",
        f"**{payload['classification']}**",
        "",
        "## Development",
        f"- interval: `{development['start']} -> {development['end_exclusive']}`",
        f"- selected independent episodes: `{development['trades']}` (`{development['trades_per_day']:.4f}` per day)",
        f"- gross / net mean: `{development['mean_gross_bps']} / {development['mean_net_bps']}` bp",
        f"- after-cost win rate: `{development['win_rate_after_cost']}`",
        f"- net t-stat: `{development['net_t_stat']}`",
        "",
        "## Untouched evaluation",
        f"- interval: `{evaluation['start']} -> {evaluation['end_exclusive']}`",
        f"- selected independent episodes: `{evaluation['trades']}` (`{evaluation['trades_per_day']:.4f}` per day)",
        f"- gross / net mean: `{evaluation['mean_gross_bps']} / {evaluation['mean_net_bps']}` bp",
        f"- after-cost win rate: `{evaluation['win_rate_after_cost']}`",
        f"- payoff ratio: `{evaluation['payoff_ratio']}`",
        f"- net t-stat: `{evaluation['net_t_stat']}`",
        f"- positive months: `{evaluation['positive_months']} / {evaluation['active_months']}`",
        f"- route counts: `{evaluation['route_counts']}`",
        f"- symbol counts: `{evaluation['symbol_counts']}`",
        "",
        "## Advance checks",
    ]
    lines.extend(f"- {name}: `{value}`" for name, value in checks.items())
    lines.extend(
        (
            "",
            "## Decision",
            payload["decision"],
            "",
            "This diagnostic does not synthesize account NAV. It only determines whether the causal route is economically strong enough to justify a frozen NautilusTrader implementation.",
        )
    )
    return "\n".join(lines) + "\n"


def execute(protocol_path: Path, output_dir: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    data_start = date.fromisoformat(protocol["data"]["start"])
    data_end = date.fromisoformat(protocol["data"]["end_exclusive"])
    own: list[pd.DataFrame] = []
    manifest: list[dict[str, Any]] = []
    for symbol in protocol["data"]["symbols"]:
        frame, records = load_symbol(
            symbol,
            data_start,
            data_end,
            output_dir / "data",
        )
        own.append(own_event_features(frame, symbol))
        manifest.extend(records)
        del frame
    write_json(
        output_dir / "data_manifest.json",
        {
            "schema": "candidate-15-v15-binance-monthly-manifest-v1",
            "dataset": protocol["data"]["dataset"],
            "start": protocol["data"]["start"],
            "end_exclusive": protocol["data"]["end_exclusive"],
            "bar_visibility": protocol["data"]["bar_visibility"],
            "files": manifest,
        },
    )
    table = build_event_table(own, protocol)
    candidates, diagnostics = walk_forward_candidates(table, protocol)
    candidates.to_csv(output_dir / "route_candidates.csv", index=False)
    selected, skips = arbitrate(candidates)
    selected.to_csv(output_dir / "selected_episodes.csv", index=False)
    write_json(output_dir / "monthly_models.json", {"months": diagnostics})
    walk = protocol["walk_forward"]
    development = summarize(
        selected,
        walk["development_start"],
        walk["development_end_exclusive"],
    )
    evaluation = summarize(
        selected,
        walk["evaluation_start"],
        walk["evaluation_end_exclusive"],
    )
    gate = protocol["advance_gate"]
    checks = {
        "positive_development_mean_net": (
            development["mean_net_bps"] is not None
            and development["mean_net_bps"] > 0.0
        ),
        "positive_evaluation_mean_net": (
            evaluation["mean_net_bps"] is not None
            and evaluation["mean_net_bps"]
            > float(gate["minimum_evaluation_mean_net_bps"])
        ),
        "evaluation_net_t_stat": (
            evaluation["net_t_stat"] is not None
            and evaluation["net_t_stat"]
            >= float(gate["minimum_evaluation_net_t_stat"])
        ),
        "positive_evaluation_month_share": (
            evaluation["positive_month_share"]
            >= float(gate["minimum_positive_evaluation_month_share"])
        ),
        "independent_frequency": (
            evaluation["trades_per_day"]
            >= float(gate["minimum_selected_trades_per_calendar_day"])
        ),
        "symbol_concentration": concentration_pass(evaluation),
    }
    passed = all(checks.values())
    if passed:
        classification = "V15_MECHANISM_ADVANCES_TO_FROZEN_NAUTILUS"
        decision = (
            "The predeclared router passed development and untouched evaluation. "
            "Freeze the policy and implement it inside the existing NautilusTrader "
            "global portfolio runner without changing route logic."
        )
    else:
        classification = (
            "V15_QUARTER_HOUR_PUBLIC_DELIVERY_REJECTED_OR_UNDERPOWERED"
        )
        decision = (
            "The route did not jointly survive costs, time stability and "
            "independent-frequency gates. Do not threshold-tune this family; "
            "retain only any route with independently positive evaluation evidence "
            "and move to a different causal scenario."
        )
    payload = {
        "schema": "candidate-15-v15-quarter-hour-summary-v1",
        "classification": classification,
        "advance_to_nautilus": passed,
        "development": development,
        "evaluation": evaluation,
        "advance_checks": checks,
        "candidate_rows": len(candidates.index),
        "selected_rows": len(selected.index),
        "arbitration_skips": dict(skips),
        "decision": decision,
    }
    write_json(output_dir / "summary.json", payload)
    (output_dir / "RESULT.md").write_text(
        render_result(payload),
        encoding="utf-8",
    )
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    execute(args.protocol.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
