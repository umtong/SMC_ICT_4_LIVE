#!/usr/bin/env python3
"""External liquidation/VWAP/basis mechanism study for Candidate 16 v9.

This is not a backtest engine. It downloads checksum-verified Binance Vision
archives and asks a narrow causal question before any strategy is built:

Do independent, dominant forced-liquidation bursts with falling open interest
and derivatives-specific basis dislocation leave enough later reversal space to
survive the project's configured round-trip costs?

The study uses only information available at each completed minute. Event
thresholds are trailing and shifted, same-direction events are de-clustered,
and all forward paths are descriptive evidence only. NautilusTrader remains
the only permitted execution/accounting engine if the mechanism is promoted.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256
import io
import json
import math
from pathlib import Path
import re
import time
from typing import Iterable
import urllib.error
import urllib.request
import zipfile

import numpy as np
import pandas as pd


BASE_URL = "https://data.binance.vision/data"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
KLINE_COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
)
LIQUIDATION_COLUMNS = (
    "time",
    "side",
    "order_type",
    "time_in_force",
    "original_quantity",
    "price",
    "average_price",
    "order_status",
    "last_filled_quantity",
    "accumulated_filled_quantity",
)
METRICS_COLUMNS = (
    "create_time",
    "symbol",
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
)
HORIZONS = (1, 5, 15, 30, 60)
ROUND_TRIP_COST_RATE = 0.0020
LIQUIDATION_QUANTILE = 0.99
MIN_NONZERO_LIQUIDATION_HISTORY = 100
DECLUSTER_MINUTES = 30
VWAP_WINDOW_MINUTES = 240
BASIS_WINDOW_MINUTES = 240


class StudyError(RuntimeError):
    """Raised when data or causal study integrity is invalid."""


@dataclass(frozen=True, slots=True)
class Archive:
    market: str
    cadence: str
    data_type: str
    symbol: str
    label: str
    interval: str | None = None

    @property
    def url(self) -> str:
        if self.market == "spot":
            root = f"{BASE_URL}/spot/{self.cadence}/{self.data_type}/{self.symbol}"
        elif self.market == "um":
            root = (
                f"{BASE_URL}/futures/um/{self.cadence}/"
                f"{self.data_type}/{self.symbol}"
            )
        else:
            raise ValueError(f"unsupported market: {self.market}")
        if self.interval is not None:
            return f"{root}/{self.interval}/{self.symbol}-{self.interval}-{self.label}.zip"
        return f"{root}/{self.symbol}-{self.data_type}-{self.label}.zip"

    @property
    def cache_name(self) -> str:
        interval = f"-{self.interval}" if self.interval else ""
        return (
            f"{self.market}-{self.cadence}-{self.data_type}-"
            f"{self.symbol}{interval}-{self.label}.zip"
        )


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _request_bytes(url: str, *, timeout: int = 180, attempts: int = 4) -> bytes:
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "SMC-ICT-4-external-mechanism-study"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (
            OSError,
            TimeoutError,
            urllib.error.HTTPError,
            urllib.error.URLError,
        ) as exc:
            last = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                raise
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise StudyError(f"failed to download {url}") from last


def download_verified(archive: Archive, cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / archive.cache_name
    checksum_path = destination.with_suffix(destination.suffix + ".CHECKSUM")
    if destination.exists() and checksum_path.exists():
        expected = checksum_path.read_text(encoding="utf-8").strip().split()[0]
        if (
            re.fullmatch(r"[0-9a-fA-F]{64}", expected)
            and _sha256_file(destination) == expected.lower()
        ):
            return destination
        destination.unlink(missing_ok=True)
        checksum_path.unlink(missing_ok=True)

    checksum_url = archive.url + ".CHECKSUM"
    checksum_text = _request_bytes(checksum_url).decode(
        "utf-8",
        errors="strict",
    ).strip()
    expected = checksum_text.split()[0].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise StudyError(f"invalid checksum for {archive.url}: {checksum_text!r}")
    payload = _request_bytes(archive.url)
    actual = sha256(payload).hexdigest()
    if actual != expected:
        raise StudyError(f"checksum mismatch for {archive.url}: {actual} != {expected}")
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(destination)
    checksum_path.write_text(checksum_text + "\n", encoding="utf-8")
    return destination


def _normalize_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _read_zip_csv(path: Path, fallback_columns: tuple[str, ...]) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(names) != 1:
            raise StudyError(f"expected one CSV in {path}, got {names}")
        raw = archive.read(names[0])
    frame = pd.read_csv(io.BytesIO(raw), header=None, dtype=str, low_memory=False)
    if frame.empty:
        return pd.DataFrame(columns=fallback_columns)
    first = [_normalize_name(value) for value in frame.iloc[0].tolist()]
    known = {_normalize_name(value) for value in fallback_columns}
    header_matches = sum(value in known for value in first)
    if header_matches >= max(2, min(4, len(fallback_columns) // 3)):
        frame = frame.iloc[1:].reset_index(drop=True)
        frame.columns = first
    else:
        if frame.shape[1] != len(fallback_columns):
            raise StudyError(
                f"unexpected column count in {path}: "
                f"{frame.shape[1]} != {len(fallback_columns)}",
            )
        frame.columns = list(fallback_columns)
    return frame


def _timestamp_ms(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    finite = numeric.dropna()
    if not finite.empty:
        median = float(finite.abs().median())
        if median >= 1e17:
            numeric = numeric / 1_000_000.0
        elif median >= 1e14:
            numeric = numeric / 1_000.0
        elif median < 1e11:
            parsed = pd.to_datetime(values, utc=True, errors="coerce")
            return (parsed.astype("int64") // 1_000_000).where(parsed.notna())
        return numeric
    parsed = pd.to_datetime(values, utc=True, errors="coerce")
    return (parsed.astype("int64") // 1_000_000).where(parsed.notna())


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def read_kline(path: Path, *, prefix: str) -> pd.DataFrame:
    frame = _read_zip_csv(path, KLINE_COLUMNS)
    if frame.empty:
        raise StudyError(f"empty kline archive: {path}")
    open_ms = _timestamp_ms(frame["open_time"])
    result = pd.DataFrame(
        {
            "minute": pd.to_datetime(
                open_ms,
                unit="ms",
                utc=True,
                errors="coerce",
            ).dt.floor("min"),
            f"{prefix}_open": _numeric(frame, "open"),
            f"{prefix}_high": _numeric(frame, "high"),
            f"{prefix}_low": _numeric(frame, "low"),
            f"{prefix}_close": _numeric(frame, "close"),
            f"{prefix}_volume": _numeric(frame, "volume"),
            f"{prefix}_quote_volume": _numeric(frame, "quote_volume"),
            f"{prefix}_taker_buy_quote": _numeric(
                frame,
                "taker_buy_quote_volume",
            ),
        },
    )
    result = result.dropna(subset=["minute", f"{prefix}_close"]).drop_duplicates(
        "minute",
        keep="last",
    )
    if result["minute"].duplicated().any() or not result["minute"].is_monotonic_increasing:
        result = result.sort_values("minute", kind="stable").drop_duplicates(
            "minute",
            keep="last",
        )
    return result.reset_index(drop=True)


def _column(frame: pd.DataFrame, *candidates: str) -> str | None:
    normalized = {_normalize_name(column): str(column) for column in frame.columns}
    for candidate in candidates:
        key = _normalize_name(candidate)
        if key in normalized:
            return normalized[key]
    return None


def read_liquidation(paths: Iterable[Path]) -> pd.DataFrame:
    records: list[pd.DataFrame] = []
    for path in paths:
        frame = _read_zip_csv(path, LIQUIDATION_COLUMNS)
        if frame.empty:
            continue
        time_col = _column(frame, "time", "trade_time", "event_time")
        side_col = _column(frame, "side")
        price_col = _column(frame, "average_price", "avg_price", "price")
        fallback_price_col = _column(frame, "price")
        cumulative_col = _column(
            frame,
            "accumulated_filled_quantity",
            "executed_qty",
            "filled_quantity",
        )
        last_col = _column(frame, "last_filled_quantity")
        original_col = _column(frame, "original_quantity", "quantity", "qty")
        if time_col is None or side_col is None or price_col is None:
            raise StudyError(
                f"cannot resolve liquidation schema in {path}: {list(frame.columns)}",
            )
        price = _numeric(frame, price_col)
        if fallback_price_col is not None:
            price = price.where(price > 0.0, _numeric(frame, fallback_price_col))
        if cumulative_col is not None:
            quantity = _numeric(frame, cumulative_col)
        else:
            quantity = pd.Series(np.nan, index=frame.index, dtype=float)
        if last_col is not None:
            quantity = quantity.where(quantity > 0.0, _numeric(frame, last_col))
        if original_col is not None:
            quantity = quantity.where(quantity > 0.0, _numeric(frame, original_col))
        if quantity.isna().all():
            raise StudyError(f"cannot resolve liquidation quantity in {path}")
        time_ms = _timestamp_ms(frame[time_col])
        current = pd.DataFrame(
            {
                "minute": pd.to_datetime(
                    time_ms,
                    unit="ms",
                    utc=True,
                    errors="coerce",
                ).dt.floor("min"),
                "side": frame[side_col].astype(str).str.upper().str.strip(),
                "notional": price * quantity,
            },
        )
        current = current[
            current["side"].isin({"BUY", "SELL"})
            & current["notional"].gt(0.0)
            & current["minute"].notna()
        ]
        records.append(current)
    if not records:
        return pd.DataFrame(
            columns=["minute", "long_liq_notional", "short_liq_notional"],
        )
    liquidations = pd.concat(records, ignore_index=True)
    grouped = (
        liquidations.pivot_table(
            index="minute",
            columns="side",
            values="notional",
            aggfunc="sum",
            fill_value=0.0,
        )
        .rename(columns={"SELL": "long_liq_notional", "BUY": "short_liq_notional"})
        .reset_index()
    )
    for column in ("long_liq_notional", "short_liq_notional"):
        if column not in grouped:
            grouped[column] = 0.0
    return grouped[["minute", "long_liq_notional", "short_liq_notional"]]


def read_metrics(paths: Iterable[Path]) -> pd.DataFrame:
    records: list[pd.DataFrame] = []
    for path in paths:
        frame = _read_zip_csv(path, METRICS_COLUMNS)
        if frame.empty:
            continue
        time_col = _column(frame, "create_time", "time", "timestamp")
        oi_col = _column(frame, "sum_open_interest", "open_interest")
        oi_value_col = _column(
            frame,
            "sum_open_interest_value",
            "open_interest_value",
        )
        if time_col is None or oi_col is None:
            raise StudyError(
                f"cannot resolve metrics schema in {path}: {list(frame.columns)}",
            )
        time_ms = _timestamp_ms(frame[time_col])
        current = pd.DataFrame(
            {
                "minute": pd.to_datetime(
                    time_ms,
                    unit="ms",
                    utc=True,
                    errors="coerce",
                ).dt.floor("min"),
                "open_interest": _numeric(frame, oi_col),
                "open_interest_value": (
                    _numeric(frame, oi_value_col)
                    if oi_value_col is not None
                    else np.nan
                ),
            },
        ).dropna(subset=["minute", "open_interest"])
        records.append(current)
    if not records:
        raise StudyError("no open-interest metrics rows")
    return (
        pd.concat(records, ignore_index=True)
        .sort_values("minute", kind="stable")
        .drop_duplicates("minute", keep="last")
        .reset_index(drop=True)
    )


def iter_dates(month: str) -> list[date]:
    start = date.fromisoformat(f"{month}-01")
    cursor = start
    values = []
    while cursor.month == start.month:
        values.append(cursor)
        cursor += timedelta(days=1)
    return values


def build_archives(symbol: str, month: str) -> dict[str, list[Archive]]:
    daily = [day.isoformat() for day in iter_dates(month)]
    return {
        "futures": [Archive("um", "monthly", "klines", symbol, month, "1m")],
        "spot": [Archive("spot", "monthly", "klines", symbol, month, "1m")],
        "mark": [
            Archive("um", "monthly", "markPriceKlines", symbol, month, "1m"),
        ],
        "index": [
            Archive("um", "monthly", "indexPriceKlines", symbol, month, "1m"),
        ],
        "liquidation": [
            Archive("um", "daily", "liquidationSnapshot", symbol, value)
            for value in daily
        ],
        "metrics": [
            Archive("um", "daily", "metrics", symbol, value)
            for value in daily
        ],
    }


def obtain_archives(symbol: str, month: str, cache: Path) -> dict[str, list[Path]]:
    groups = build_archives(symbol, month)
    all_archives = [archive for values in groups.values() for archive in values]
    resolved: dict[Archive, Path] = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(download_verified, archive, cache): archive
            for archive in all_archives
        }
        for future in as_completed(futures):
            archive = futures[future]
            try:
                resolved[archive] = future.result()
            except urllib.error.HTTPError as exc:
                if exc.code == 404 and archive.data_type == "liquidationSnapshot":
                    continue
                raise
    result: dict[str, list[Path]] = {}
    for name, archives in groups.items():
        result[name] = [resolved[archive] for archive in archives if archive in resolved]
    if not result["liquidation"]:
        raise StudyError(f"no liquidationSnapshot archives for {symbol} {month}")
    if len(result["metrics"]) < max(20, len(iter_dates(month)) - 5):
        raise StudyError(f"insufficient metrics coverage for {symbol} {month}")
    return result


def _rolling_mad(values: pd.Series, window: int, minimum: int) -> pd.Series:
    def mad(array: np.ndarray) -> float:
        finite = array[np.isfinite(array)]
        if len(finite) < minimum:
            return np.nan
        center = np.median(finite)
        return float(np.median(np.abs(finite - center)))

    return values.rolling(window, min_periods=minimum).apply(mad, raw=True).shift(1)


def build_panel(symbol: str, month: str, cache: Path) -> pd.DataFrame:
    paths = obtain_archives(symbol, month, cache / symbol)
    futures = read_kline(paths["futures"][0], prefix="perp")
    spot = read_kline(paths["spot"][0], prefix="spot")
    mark = read_kline(paths["mark"][0], prefix="mark")
    index = read_kline(paths["index"][0], prefix="index")
    liquidations = read_liquidation(paths["liquidation"])
    metrics = read_metrics(paths["metrics"])

    panel = futures.merge(
        spot[
            [
                "minute",
                "spot_open",
                "spot_high",
                "spot_low",
                "spot_close",
                "spot_volume",
                "spot_quote_volume",
            ]
        ],
        on="minute",
        how="inner",
        validate="one_to_one",
    )
    panel = panel.merge(
        mark[["minute", "mark_close"]],
        on="minute",
        how="inner",
        validate="one_to_one",
    ).merge(
        index[["minute", "index_close"]],
        on="minute",
        how="inner",
        validate="one_to_one",
    )
    panel = panel.merge(liquidations, on="minute", how="left", validate="one_to_one")
    panel[["long_liq_notional", "short_liq_notional"]] = panel[
        ["long_liq_notional", "short_liq_notional"]
    ].fillna(0.0)
    panel = pd.merge_asof(
        panel.sort_values("minute"),
        metrics.sort_values("minute"),
        on="minute",
        direction="backward",
        tolerance=pd.Timedelta(minutes=6),
    )
    if len(panel) < 40_000:
        raise StudyError(f"insufficient synchronized minute rows for {symbol}: {len(panel)}")

    panel["symbol"] = symbol
    panel["perp_ret_1m"] = np.log(panel["perp_close"]).diff()
    panel["spot_ret_1m"] = np.log(panel["spot_close"]).diff()
    panel["flow_1m"] = (
        2.0
        * panel["perp_taker_buy_quote"]
        / panel["perp_quote_volume"].replace(0.0, np.nan)
        - 1.0
    )
    numerator = (panel["perp_close"] * panel["perp_volume"]).rolling(
        VWAP_WINDOW_MINUTES,
        min_periods=60,
    ).sum()
    denominator = panel["perp_volume"].rolling(
        VWAP_WINDOW_MINUTES,
        min_periods=60,
    ).sum()
    panel["rolling_vwap_4h"] = numerator / denominator.replace(0.0, np.nan)

    panel["perp_spot_basis"] = panel["perp_close"] / panel["spot_close"] - 1.0
    panel["mark_index_basis"] = panel["mark_close"] / panel["index_close"] - 1.0
    for column in ("perp_spot_basis", "mark_index_basis"):
        center = panel[column].rolling(
            BASIS_WINDOW_MINUTES,
            min_periods=120,
        ).median().shift(1)
        scale = 1.4826 * _rolling_mad(panel[column], BASIS_WINDOW_MINUTES, 120)
        panel[f"{column}_center"] = center
        panel[f"{column}_scale"] = scale.clip(lower=1e-6)

    panel["oi_change_15m"] = panel["open_interest"] / panel["open_interest"].shift(15) - 1.0
    panel["liq_total"] = panel["long_liq_notional"] + panel["short_liq_notional"]
    panel["dominant_liq"] = panel[
        ["long_liq_notional", "short_liq_notional"]
    ].max(axis=1)
    panel["liq_dominance"] = panel["dominant_liq"] / panel["liq_total"].replace(
        0.0,
        np.nan,
    )
    panel["event_direction"] = np.where(
        panel["short_liq_notional"] > panel["long_liq_notional"],
        1,
        -1,
    )
    nonzero = panel["dominant_liq"].where(panel["dominant_liq"] > 0.0)
    panel["liq_threshold_99"] = (
        nonzero.rolling(
            10_080,
            min_periods=MIN_NONZERO_LIQUIDATION_HISTORY,
        )
        .quantile(LIQUIDATION_QUANTILE)
        .shift(1)
    )
    panel["liq_share_of_perp_volume"] = panel["dominant_liq"] / panel[
        "perp_quote_volume"
    ].replace(0.0, np.nan)
    direction = panel["event_direction"].astype(float)
    panel["directional_perp_return"] = direction * panel["perp_ret_1m"]
    panel["directional_spot_return"] = direction * panel["spot_ret_1m"]
    panel["futures_lead_return"] = direction * (
        panel["perp_ret_1m"] - panel["spot_ret_1m"]
    )
    panel["directional_vwap_deviation"] = direction * np.log(
        panel["perp_close"] / panel["rolling_vwap_4h"],
    )
    panel["perp_basis_z_directional"] = direction * (
        panel["perp_spot_basis"] - panel["perp_spot_basis_center"]
    ) / panel["perp_spot_basis_scale"]
    panel["mark_basis_z_directional"] = direction * (
        panel["mark_index_basis"] - panel["mark_index_basis_center"]
    ) / panel["mark_index_basis_scale"]

    candidate = (
        panel["dominant_liq"].gt(panel["liq_threshold_99"])
        & panel["liq_dominance"].ge(0.80)
        & panel["directional_perp_return"].gt(0.0)
        & panel["directional_vwap_deviation"].gt(0.0)
        & panel["open_interest"].notna()
    )
    panel["event_candidate"] = candidate.fillna(False)
    return panel


def decluster_events(panel: pd.DataFrame) -> pd.DataFrame:
    positions = np.flatnonzero(panel["event_candidate"].to_numpy(dtype=bool))
    kept: list[int] = []
    last_by_direction = {-1: -10**9, 1: -10**9}
    for position in positions:
        direction = int(panel.iloc[position]["event_direction"])
        if position - last_by_direction[direction] < DECLUSTER_MINUTES:
            continue
        kept.append(position)
        last_by_direction[direction] = position
    if not kept:
        return pd.DataFrame()
    events = panel.iloc[kept].copy()
    events["panel_position"] = kept

    oi_forced = events["oi_change_15m"] < 0.0
    basis_forced = (
        (events["perp_basis_z_directional"] >= 1.0)
        | (events["mark_basis_z_directional"] >= 1.0)
    )
    derivatives_lead = events["futures_lead_return"] > 0.0
    spot_confirms = events["directional_spot_return"] > 0.0
    events["regime"] = np.select(
        [
            oi_forced & basis_forced & derivatives_lead,
            oi_forced & derivatives_lead,
            spot_confirms & ~derivatives_lead & ~oi_forced,
        ],
        [
            "FORCED_BASIS_DISLOCATION",
            "FORCED_OI_DERIVATIVES_LEAD",
            "SPOT_CONFIRMED_INFORMATION_MOVE",
        ],
        default="UNRESOLVED",
    )

    log_close = np.log(panel["perp_close"])
    for horizon in HORIZONS:
        forward = log_close.shift(-horizon) - log_close
        events[f"reversal_return_{horizon}m"] = (
            -events["event_direction"].astype(float)
            * forward.iloc[kept].to_numpy()
        )
        events[f"continuation_return_{horizon}m"] = -events[
            f"reversal_return_{horizon}m"
        ]

    reversal_mfe_30 = []
    reversal_mae_30 = []
    vwap_reclaim_60 = []
    for position, row in zip(kept, events.itertuples(index=False)):
        future30 = panel.iloc[position + 1 : position + 31]
        future60 = panel.iloc[position + 1 : position + 61]
        entry = float(row.perp_close)
        direction = int(row.event_direction)
        reversal_side = -direction
        if future30.empty:
            reversal_mfe_30.append(np.nan)
            reversal_mae_30.append(np.nan)
        elif reversal_side > 0:
            reversal_mfe_30.append(float(future30["perp_high"].max() / entry - 1.0))
            reversal_mae_30.append(float(future30["perp_low"].min() / entry - 1.0))
        else:
            reversal_mfe_30.append(float(1.0 - future30["perp_low"].min() / entry))
            reversal_mae_30.append(float(1.0 - future30["perp_high"].max() / entry))
        vwap = float(row.rolling_vwap_4h)
        if future60.empty or not math.isfinite(vwap):
            vwap_reclaim_60.append(False)
        elif reversal_side > 0:
            vwap_reclaim_60.append(bool(future60["perp_high"].max() >= vwap))
        else:
            vwap_reclaim_60.append(bool(future60["perp_low"].min() <= vwap))
    events["reversal_mfe_30m"] = reversal_mfe_30
    events["reversal_mae_30m"] = reversal_mae_30
    events["vwap_reclaim_60m"] = vwap_reclaim_60
    return events


def _finite_series(frame: pd.DataFrame, column: str) -> pd.Series:
    return (
        pd.to_numeric(frame[column], errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )


def _stat(values: pd.Series, method: str, *args: float) -> float | None:
    if values.empty:
        return None
    value = getattr(values, method)(*args)
    return float(value) if math.isfinite(float(value)) else None


def summarize_group(frame: pd.DataFrame) -> dict[str, object]:
    result: dict[str, object] = {
        "count": int(len(frame)),
        "symbols": (
            {
                symbol: int((frame["symbol"] == symbol).sum())
                for symbol in sorted(frame["symbol"].unique())
            }
            if not frame.empty
            else {}
        ),
        "long_liquidation_events": (
            int((frame["event_direction"] == -1).sum()) if not frame.empty else 0
        ),
        "short_liquidation_events": (
            int((frame["event_direction"] == 1).sum()) if not frame.empty else 0
        ),
    }
    if frame.empty:
        return result
    for horizon in HORIZONS:
        values = _finite_series(frame, f"reversal_return_{horizon}m")
        result[f"reversal_{horizon}m"] = {
            "mean": _stat(values, "mean"),
            "median": _stat(values, "median"),
            "q25": _stat(values, "quantile", 0.25),
            "q75": _stat(values, "quantile", 0.75),
            "positive_rate": (
                float((values > 0.0).mean()) if not values.empty else None
            ),
            "cost_after_hit_rate": (
                float((values > ROUND_TRIP_COST_RATE).mean())
                if not values.empty
                else None
            ),
        }
    result["vwap_reclaim_60m_rate"] = float(frame["vwap_reclaim_60m"].mean())
    result["median_reversal_mfe_30m"] = _stat(
        _finite_series(frame, "reversal_mfe_30m"),
        "median",
    )
    result["median_reversal_mae_30m"] = _stat(
        _finite_series(frame, "reversal_mae_30m"),
        "median",
    )
    result["median_liq_share_of_perp_volume"] = _stat(
        _finite_series(frame, "liq_share_of_perp_volume"),
        "median",
    )
    result["median_oi_change_15m"] = _stat(
        _finite_series(frame, "oi_change_15m"),
        "median",
    )
    result["median_futures_lead_return"] = _stat(
        _finite_series(frame, "futures_lead_return"),
        "median",
    )
    return result


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def run(month: str, cache: Path, output: Path) -> dict[str, object]:
    all_events: list[pd.DataFrame] = []
    coverage: dict[str, object] = {}
    for symbol in SYMBOLS:
        panel = build_panel(symbol, month, cache)
        events = decluster_events(panel)
        coverage[symbol] = {
            "minute_rows": int(len(panel)),
            "liquidation_minutes": int((panel["liq_total"] > 0.0).sum()),
            "candidate_minutes_before_decluster": int(panel["event_candidate"].sum()),
            "independent_events": int(len(events)),
            "start": panel["minute"].min().isoformat(),
            "end": panel["minute"].max().isoformat(),
        }
        panel.to_parquet(output / f"{symbol}-panel.parquet", index=False)
        if not events.empty:
            all_events.append(events)

    events = pd.concat(all_events, ignore_index=True) if all_events else pd.DataFrame()
    if not events.empty:
        events = events.sort_values(["minute", "symbol"], kind="stable").reset_index(
            drop=True,
        )
        events.to_csv(output / "events.csv", index=False)

    summaries = {"ALL": summarize_group(events)}
    if not events.empty:
        for regime, group in events.groupby("regime", sort=True):
            summaries[str(regime)] = summarize_group(group)
        for direction, label in (
            (-1, "LONG_LIQUIDATIONS"),
            (1, "SHORT_LIQUIDATIONS"),
        ):
            summaries[label] = summarize_group(
                events[events["event_direction"] == direction],
            )

    forced = (
        events[
            events["regime"].isin(
                {"FORCED_BASIS_DISLOCATION", "FORCED_OI_DERIVATIVES_LEAD"},
            )
        ]
        if not events.empty
        else events
    )
    forced_summary = summarize_group(forced)
    thirty = forced_summary.get("reversal_30m", {})
    count = int(forced_summary.get("count", 0))
    median30 = thirty.get("median") if isinstance(thirty, dict) else None
    hit30 = thirty.get("cost_after_hit_rate") if isinstance(thirty, dict) else None
    reclaim = forced_summary.get("vwap_reclaim_60m_rate")
    promote = bool(
        count >= 12
        and median30 is not None
        and float(median30) >= ROUND_TRIP_COST_RATE
        and hit30 is not None
        and float(hit30) >= 0.55
        and reclaim is not None
        and float(reclaim) >= 0.50
    )
    decision = (
        "PROMOTE_FORCED_LIQUIDATION_DISLOCATION_TO_CAUSAL_NAUTILUS_PROTOTYPE"
        if promote
        else (
            "DO_NOT_BUILD_TRADING_STRATEGY_FROM_THIS_MONTH; "
            "MECHANISM_SPACE_INSUFFICIENT_OR_SPARSE"
        )
    )
    result = {
        "schema": "candidate-16-v9-external-liquidation-study-v1",
        "role": "mechanism event study; no fill, PnL, NAV, or strategy claim",
        "month": month,
        "symbols": list(SYMBOLS),
        "data_source": "checksum-verified Binance Vision public archives",
        "external_reuse": {
            "url_and_type_contract": "aoki-h-jp/binance-bulk-downloader",
            "adaptation": (
                "date-bounded checksum-verified retrieval plus project-specific "
                "causal event study"
            ),
        },
        "cost_assumption": {
            "round_trip_rate": ROUND_TRIP_COST_RATE,
            "meaning": "7.5 bps fee plus 2.5 bps adverse slippage per side",
        },
        "event_contract": {
            "liquidation_threshold": (
                "current dominant side > shifted trailing 99th percentile "
                "of nonzero minutes"
            ),
            "minimum_history": MIN_NONZERO_LIQUIDATION_HISTORY,
            "dominance": 0.80,
            "must_move_with_liquidation": True,
            "must_be_outside_trailing_4h_vwap": True,
            "same_direction_decluster_minutes": DECLUSTER_MINUTES,
            "liquidation_stream_limitation": (
                "public snapshot is a lower bound after 2021-04-27"
            ),
        },
        "coverage": coverage,
        "summaries": summaries,
        "promotion_checks": {
            "forced_events_at_least_12": count >= 12,
            "median_30m_reversal_covers_round_trip_cost": (
                median30 is not None and float(median30) >= ROUND_TRIP_COST_RATE
            ),
            "30m_cost_after_hit_rate_at_least_55pct": (
                hit30 is not None and float(hit30) >= 0.55
            ),
            "vwap_reclaim_60m_rate_at_least_50pct": (
                reclaim is not None and float(reclaim) >= 0.50
            ),
        },
        "promote": promote,
        "decision": decision,
    }
    write_json(output / "study.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", required=True, help="development month YYYY-MM")
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}", args.month):
        raise SystemExit("--month must be YYYY-MM")
    args.output.mkdir(parents=True, exist_ok=True)
    result = run(args.month, args.cache.resolve(), args.output.resolve())
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
