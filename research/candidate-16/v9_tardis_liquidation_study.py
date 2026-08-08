#!/usr/bin/env python3
"""Cross-regime liquidation/price-discovery study from free Tardis samples.

External idea decomposition
---------------------------
Practitioner liquidation/VWAP systems often fade every large wick.  Market
microstructure work instead suggests two different latent states:

1. forced deleveraging: liquidation pressure, falling open interest and a
   derivatives-specific price/basis lead can overshoot fair value and later
   recover;
2. information repricing: spot participates while leverage is not being removed,
   so the same visible liquidation burst can be continuation rather than fade.

This study tests that router before building a strategy.  It downloads the
first day of every month (free without an API key) from Tardis.dev for actual
Binance Futures liquidations and derivative tickers, plus checksum-verified
Binance Vision spot/perpetual minute bars.  Thresholds are trailing and shifted,
all observations are complete-minute causal, same-direction events are
within-symbol de-clustered, and simultaneous cross-asset cascades are collapsed
into one global causal episode.

This module creates no fills, PnL, account, or NAV.  Only a promoted mechanism
may be implemented in NautilusTrader.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import gzip
import io
import json
import math
from pathlib import Path
import re
import time
from typing import Iterable
import urllib.error
import urllib.request

import numpy as np
import pandas as pd

from v9_liquidation_event_study import Archive
from v9_liquidation_event_study import StudyError
from v9_liquidation_event_study import download_verified as download_binance_verified
from v9_liquidation_event_study import read_kline


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
START_MONTH = "2021-09"
END_MONTH = "2023-12"
TARDIS_ROOT = "https://datasets.tardis.dev/v1/binance-futures"
ROUND_TRIP_COST_RATE = 0.0020
LIQUIDATION_QUANTILE = 0.99
MIN_PRIOR_NONZERO_LIQUIDATION_MINUTES = 100
DOMINANCE_MIN = 0.80
WITHIN_SYMBOL_DECLUSTER_MINUTES = 30
GLOBAL_CLUSTER_MINUTES = 3
VWAP_WINDOW_MINUTES = 240
BASIS_WINDOW_MINUTES = 240
HORIZONS = (1, 5, 15, 30, 60)

LIQUIDATION_COLUMNS = (
    "exchange",
    "symbol",
    "timestamp",
    "local_timestamp",
    "id",
    "side",
    "price",
    "amount",
)
DERIVATIVE_COLUMNS = (
    "exchange",
    "symbol",
    "timestamp",
    "local_timestamp",
    "funding_timestamp",
    "funding_rate",
    "predicted_funding_rate",
    "open_interest",
    "last_price",
    "index_price",
    "mark_price",
)


@dataclass(frozen=True, slots=True)
class TardisArchive:
    data_type: str
    day: date
    symbol: str

    @property
    def url(self) -> str:
        return (
            f"{TARDIS_ROOT}/{self.data_type}/"
            f"{self.day:%Y/%m/%d}/{self.symbol}.csv.gz"
        )

    @property
    def cache_name(self) -> str:
        return f"tardis-{self.data_type}-{self.symbol}-{self.day}.csv.gz"


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _request_bytes(url: str, *, attempts: int = 5, timeout: int = 300) -> bytes:
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "SMC-ICT-4-external-alpha-study"},
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
            if isinstance(exc, urllib.error.HTTPError) and exc.code in {401, 403, 404}:
                raise
            if attempt + 1 < attempts:
                time.sleep(2**attempt)
    raise StudyError(f"failed to download {url}") from last


def _verify_gzip_csv(payload: bytes, archive: TardisArchive) -> tuple[str, int]:
    try:
        raw = gzip.decompress(payload)
    except (OSError, EOFError) as exc:
        raise StudyError(f"invalid gzip payload: {archive.url}") from exc
    if not raw or b"," not in raw.splitlines()[0]:
        raise StudyError(f"invalid Tardis CSV payload: {archive.url}")
    return sha256(payload).hexdigest(), len(raw)


def download_tardis(archive: TardisArchive, cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / archive.cache_name
    digest_path = destination.with_suffix(destination.suffix + ".sha256")
    if destination.exists() and digest_path.exists():
        expected = digest_path.read_text(encoding="utf-8").strip().split()[0]
        if re.fullmatch(r"[0-9a-f]{64}", expected) and _sha256_file(destination) == expected:
            _verify_gzip_csv(destination.read_bytes(), archive)
            return destination
        destination.unlink(missing_ok=True)
        digest_path.unlink(missing_ok=True)

    payload = _request_bytes(archive.url)
    digest, raw_size = _verify_gzip_csv(payload, archive)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(destination)
    digest_path.write_text(f"{digest}  {archive.cache_name}\n", encoding="utf-8")
    destination.with_suffix(destination.suffix + ".integrity.json").write_text(
        json.dumps(
            {
                "url": archive.url,
                "compressed_size_bytes": len(payload),
                "uncompressed_size_bytes": raw_size,
                "sha256": digest,
                "gzip_crc_verified": True,
                "free_sample_contract": "first calendar day of month",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return destination


def _months(start: str, end: str) -> list[date]:
    current = pd.Period(start, freq="M")
    final = pd.Period(end, freq="M")
    if current > final:
        raise ValueError("start month must not exceed end month")
    result: list[date] = []
    while current <= final:
        result.append(date(current.year, current.month, 1))
        current += 1
    return result


def _read_tardis(path: Path, columns: tuple[str, ...]) -> pd.DataFrame:
    frame = pd.read_csv(path, compression="gzip", low_memory=False)
    normalized = [str(column).strip().lower() for column in frame.columns]
    frame.columns = normalized
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise StudyError(f"missing Tardis columns in {path}: {missing}")
    return frame[list(columns)].copy()


def read_tardis_liquidations(path: Path) -> pd.DataFrame:
    frame = _read_tardis(path, LIQUIDATION_COLUMNS)
    timestamp = pd.to_numeric(frame["timestamp"], errors="coerce")
    price = pd.to_numeric(frame["price"], errors="coerce")
    amount = pd.to_numeric(frame["amount"], errors="coerce")
    side = frame["side"].astype(str).str.lower().str.strip()
    current = pd.DataFrame(
        {
            "minute": pd.to_datetime(
                timestamp,
                unit="us",
                utc=True,
                errors="coerce",
            ).dt.floor("min"),
            "side": side,
            "notional": price * amount,
        },
    )
    current = current[
        current["minute"].notna()
        & current["side"].isin({"buy", "sell"})
        & current["notional"].gt(0.0)
    ]
    grouped = (
        current.pivot_table(
            index="minute",
            columns="side",
            values="notional",
            aggfunc="sum",
            fill_value=0.0,
        )
        .rename(columns={"sell": "long_liq_notional", "buy": "short_liq_notional"})
        .reset_index()
    )
    for column in ("long_liq_notional", "short_liq_notional"):
        if column not in grouped:
            grouped[column] = 0.0
    return grouped[["minute", "long_liq_notional", "short_liq_notional"]]


def read_tardis_derivative(path: Path) -> pd.DataFrame:
    frame = _read_tardis(path, DERIVATIVE_COLUMNS)
    timestamp = pd.to_numeric(frame["timestamp"], errors="coerce")
    result = pd.DataFrame(
        {
            "minute": pd.to_datetime(
                timestamp,
                unit="us",
                utc=True,
                errors="coerce",
            ).dt.floor("min"),
            "open_interest": pd.to_numeric(frame["open_interest"], errors="coerce"),
            "last_price_tick": pd.to_numeric(frame["last_price"], errors="coerce"),
            "index_price_tick": pd.to_numeric(frame["index_price"], errors="coerce"),
            "mark_price_tick": pd.to_numeric(frame["mark_price"], errors="coerce"),
        },
    ).dropna(subset=["minute"])
    result = result.sort_values("minute", kind="stable")
    return result.groupby("minute", as_index=False, sort=True).last()


def _binance_archives(symbol: str, day: date) -> tuple[Archive, Archive]:
    label = day.isoformat()
    return (
        Archive("um", "daily", "klines", symbol, label, "1m"),
        Archive("spot", "daily", "klines", symbol, label, "1m"),
    )


def obtain_day(symbol: str, day: date, cache: Path) -> dict[str, Path]:
    liquidation = TardisArchive("liquidations", day, symbol)
    derivative = TardisArchive("derivative_ticker", day, symbol)
    futures_archive, spot_archive = _binance_archives(symbol, day)
    return {
        "liquidation": download_tardis(liquidation, cache / "tardis"),
        "derivative": download_tardis(derivative, cache / "tardis"),
        "futures": download_binance_verified(futures_archive, cache / "binance"),
        "spot": download_binance_verified(spot_archive, cache / "binance"),
    }


def _rolling_mad(values: pd.Series, window: int, minimum: int) -> pd.Series:
    def mad(array: np.ndarray) -> float:
        finite = array[np.isfinite(array)]
        if len(finite) < minimum:
            return np.nan
        center = float(np.median(finite))
        return float(np.median(np.abs(finite - center)))

    return values.rolling(window, min_periods=minimum).apply(mad, raw=True).shift(1)


def build_day(symbol: str, day: date, paths: dict[str, Path]) -> pd.DataFrame:
    perp = read_kline(paths["futures"], prefix="perp")
    spot = read_kline(paths["spot"], prefix="spot")
    liquidation = read_tardis_liquidations(paths["liquidation"])
    derivative = read_tardis_derivative(paths["derivative"])

    panel = perp.merge(
        spot[
            [
                "minute",
                "spot_open",
                "spot_high",
                "spot_low",
                "spot_close",
                "spot_quote_volume",
            ]
        ],
        on="minute",
        how="inner",
        validate="one_to_one",
    )
    panel = panel.merge(liquidation, on="minute", how="left", validate="one_to_one")
    panel = pd.merge_asof(
        panel.sort_values("minute"),
        derivative.sort_values("minute"),
        on="minute",
        direction="backward",
        tolerance=pd.Timedelta(minutes=6),
    )
    panel[["long_liq_notional", "short_liq_notional"]] = panel[
        ["long_liq_notional", "short_liq_notional"]
    ].fillna(0.0)
    if len(panel) < 1_400:
        raise StudyError(f"incomplete synchronized day: {symbol} {day} rows={len(panel)}")
    panel["symbol"] = symbol
    panel["sample_day"] = day.isoformat()

    panel["perp_ret_1m"] = np.log(panel["perp_close"]).diff()
    panel["spot_ret_1m"] = np.log(panel["spot_close"]).diff()
    panel["perp_spot_basis"] = panel["perp_close"] / panel["spot_close"] - 1.0
    panel["mark_index_basis"] = panel["mark_price_tick"] / panel["index_price_tick"] - 1.0
    panel["oi_change_15m"] = panel["open_interest"] / panel["open_interest"].shift(15) - 1.0

    vwap_numerator = (panel["perp_close"] * panel["perp_volume"]).rolling(
        VWAP_WINDOW_MINUTES,
        min_periods=60,
    ).sum()
    vwap_denominator = panel["perp_volume"].rolling(
        VWAP_WINDOW_MINUTES,
        min_periods=60,
    ).sum()
    panel["rolling_vwap_4h"] = vwap_numerator / vwap_denominator.replace(0.0, np.nan)

    for column in ("perp_spot_basis", "mark_index_basis"):
        center = panel[column].rolling(
            BASIS_WINDOW_MINUTES,
            min_periods=120,
        ).median().shift(1)
        scale = 1.4826 * _rolling_mad(panel[column], BASIS_WINDOW_MINUTES, 120)
        panel[f"{column}_center"] = center
        panel[f"{column}_scale"] = scale.clip(lower=1e-6)

    panel["dominant_liq"] = panel[
        ["long_liq_notional", "short_liq_notional"]
    ].max(axis=1)
    panel["liq_total"] = panel["long_liq_notional"] + panel["short_liq_notional"]
    panel["liq_dominance"] = panel["dominant_liq"] / panel["liq_total"].replace(0.0, np.nan)
    # +1: short liquidations/buy pressure/up move; -1: long liquidations/sell pressure/down move.
    panel["event_direction"] = np.where(
        panel["short_liq_notional"] > panel["long_liq_notional"],
        1,
        -1,
    )
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
    panel["liq_share_of_perp_volume"] = panel["dominant_liq"] / panel[
        "perp_quote_volume"
    ].replace(0.0, np.nan)
    return panel


def apply_causal_event_thresholds(panel: pd.DataFrame) -> pd.DataFrame:
    result: list[pd.DataFrame] = []
    for symbol, group in panel.groupby("symbol", sort=False):
        group = group.sort_values("minute", kind="stable").copy()
        nonzero = group["dominant_liq"].where(group["dominant_liq"] > 0.0)
        group["liq_threshold_99"] = (
            nonzero.expanding(min_periods=MIN_PRIOR_NONZERO_LIQUIDATION_MINUTES)
            .quantile(LIQUIDATION_QUANTILE)
            .shift(1)
        )
        group["event_candidate"] = (
            group["dominant_liq"].gt(group["liq_threshold_99"])
            & group["liq_dominance"].ge(DOMINANCE_MIN)
            & group["directional_perp_return"].gt(0.0)
            & group["directional_vwap_deviation"].gt(0.0)
            & group["open_interest"].notna()
        ).fillna(False)
        result.append(group)
    return pd.concat(result, ignore_index=True)


def _within_symbol_decluster(panel: pd.DataFrame) -> pd.DataFrame:
    kept: list[int] = []
    for symbol, group in panel.groupby("symbol", sort=False):
        positions = group.index[group["event_candidate"]].tolist()
        last = {-1: pd.Timestamp.min.tz_localize("UTC"), 1: pd.Timestamp.min.tz_localize("UTC")}
        for position in positions:
            row = panel.loc[position]
            direction = int(row["event_direction"])
            moment = pd.Timestamp(row["minute"])
            if moment - last[direction] < pd.Timedelta(minutes=WITHIN_SYMBOL_DECLUSTER_MINUTES):
                continue
            kept.append(position)
            last[direction] = moment
    return panel.loc[sorted(kept)].copy() if kept else pd.DataFrame()


def _global_cluster(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events
    ordered = events.sort_values("minute", kind="stable").copy()
    clusters: list[list[int]] = []
    current: list[int] = []
    anchor: pd.Timestamp | None = None
    direction: int | None = None
    for index, row in ordered.iterrows():
        moment = pd.Timestamp(row["minute"])
        row_direction = int(row["event_direction"])
        if (
            anchor is None
            or direction != row_direction
            or moment - anchor > pd.Timedelta(minutes=GLOBAL_CLUSTER_MINUTES)
        ):
            if current:
                clusters.append(current)
            current = [index]
            anchor = moment
            direction = row_direction
        else:
            current.append(index)
    if current:
        clusters.append(current)

    representatives: list[pd.Series] = []
    for cluster_id, members in enumerate(clusters, start=1):
        group = ordered.loc[members]
        representative_index = group["liq_share_of_perp_volume"].fillna(-np.inf).idxmax()
        representative = ordered.loc[representative_index].copy()
        representative["global_cluster_id"] = cluster_id
        representative["cluster_symbol_count"] = int(group["symbol"].nunique())
        representative["cluster_symbols"] = ",".join(sorted(group["symbol"].unique()))
        representative["cluster_total_liquidation_notional"] = float(group["dominant_liq"].sum())
        representative["cluster_first_minute"] = group["minute"].min()
        representative["cluster_last_minute"] = group["minute"].max()
        representatives.append(representative)
    return pd.DataFrame(representatives).reset_index(drop=True)


def classify_and_score(panel: pd.DataFrame) -> pd.DataFrame:
    candidates = _within_symbol_decluster(panel)
    events = _global_cluster(candidates)
    if events.empty:
        return events

    oi_forced = events["oi_change_15m"] < 0.0
    derivatives_lead = events["futures_lead_return"] > 0.0
    basis_dislocation = (
        events["perp_basis_z_directional"].ge(1.0)
        | events["mark_basis_z_directional"].ge(1.0)
    )
    spot_information = (
        events["directional_spot_return"].gt(0.0)
        & events["futures_lead_return"].le(0.0)
        & events["oi_change_15m"].ge(0.0)
    )
    events["regime"] = np.select(
        [
            oi_forced & derivatives_lead & basis_dislocation,
            oi_forced & derivatives_lead,
            spot_information,
        ],
        [
            "FORCED_BASIS_DISLOCATION",
            "FORCED_OI_DERIVATIVES_LEAD",
            "SPOT_CONFIRMED_INFORMATION_MOVE",
        ],
        default="UNRESOLVED",
    )

    lookups = {
        symbol: group.sort_values("minute", kind="stable").set_index("minute")
        for symbol, group in panel.groupby("symbol", sort=False)
    }
    for horizon in HORIZONS:
        reversal: list[float] = []
        continuation: list[float] = []
        for row in events.itertuples(index=False):
            group = lookups[str(row.symbol)]
            moment = pd.Timestamp(row.minute)
            future_time = moment + pd.Timedelta(minutes=horizon)
            if future_time not in group.index:
                reversal.append(np.nan)
                continuation.append(np.nan)
                continue
            future = float(group.loc[future_time, "perp_close"])
            current = float(row.perp_close)
            directional = int(row.event_direction) * math.log(future / current)
            continuation.append(directional)
            reversal.append(-directional)
        events[f"reversal_return_{horizon}m"] = reversal
        events[f"continuation_return_{horizon}m"] = continuation

    reversal_mfe: list[float] = []
    reversal_mae: list[float] = []
    continuation_mfe: list[float] = []
    continuation_mae: list[float] = []
    vwap_reclaim: list[bool] = []
    for row in events.itertuples(index=False):
        group = lookups[str(row.symbol)]
        moment = pd.Timestamp(row.minute)
        future30 = group.loc[
            (group.index > moment) & (group.index <= moment + pd.Timedelta(minutes=30))
        ]
        future60 = group.loc[
            (group.index > moment) & (group.index <= moment + pd.Timedelta(minutes=60))
        ]
        entry = float(row.perp_close)
        event_direction = int(row.event_direction)
        reversal_side = -event_direction
        if future30.empty:
            reversal_mfe.append(np.nan)
            reversal_mae.append(np.nan)
            continuation_mfe.append(np.nan)
            continuation_mae.append(np.nan)
        else:
            high = float(future30["perp_high"].max())
            low = float(future30["perp_low"].min())
            if reversal_side > 0:
                reversal_mfe.append(high / entry - 1.0)
                reversal_mae.append(low / entry - 1.0)
                continuation_mfe.append(1.0 - low / entry)
                continuation_mae.append(1.0 - high / entry)
            else:
                reversal_mfe.append(1.0 - low / entry)
                reversal_mae.append(1.0 - high / entry)
                continuation_mfe.append(high / entry - 1.0)
                continuation_mae.append(low / entry - 1.0)
        vwap = float(row.rolling_vwap_4h)
        if future60.empty or not math.isfinite(vwap):
            vwap_reclaim.append(False)
        elif reversal_side > 0:
            vwap_reclaim.append(bool(future60["perp_high"].max() >= vwap))
        else:
            vwap_reclaim.append(bool(future60["perp_low"].min() <= vwap))
    events["reversal_mfe_30m"] = reversal_mfe
    events["reversal_mae_30m"] = reversal_mae
    events["continuation_mfe_30m"] = continuation_mfe
    events["continuation_mae_30m"] = continuation_mae
    events["vwap_reclaim_60m"] = vwap_reclaim
    return events


def _finite(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()


def _summary(frame: pd.DataFrame, policy: str) -> dict[str, object]:
    result: dict[str, object] = {
        "count": int(len(frame)),
        "symbols": (
            {symbol: int((frame["symbol"] == symbol).sum()) for symbol in SYMBOLS}
            if not frame.empty
            else {symbol: 0 for symbol in SYMBOLS}
        ),
        "systemic_cluster_count": (
            int((frame["cluster_symbol_count"] >= 2).sum()) if not frame.empty else 0
        ),
    }
    prefix = "reversal" if policy == "reversal" else "continuation"
    for horizon in HORIZONS:
        values = _finite(frame, f"{prefix}_return_{horizon}m") if not frame.empty else pd.Series(dtype=float)
        result[f"{prefix}_{horizon}m"] = {
            "mean": float(values.mean()) if not values.empty else None,
            "median": float(values.median()) if not values.empty else None,
            "positive_rate": float((values > 0.0).mean()) if not values.empty else None,
            "cost_after_hit_rate": (
                float((values > ROUND_TRIP_COST_RATE).mean()) if not values.empty else None
            ),
            "q25": float(values.quantile(0.25)) if not values.empty else None,
            "q75": float(values.quantile(0.75)) if not values.empty else None,
        }
    if not frame.empty:
        result["median_mfe_30m"] = float(_finite(frame, f"{prefix}_mfe_30m").median())
        result["median_mae_30m"] = float(_finite(frame, f"{prefix}_mae_30m").median())
        result["vwap_reclaim_60m_rate"] = float(frame["vwap_reclaim_60m"].mean())
        result["median_oi_change_15m"] = float(_finite(frame, "oi_change_15m").median())
        result["median_futures_lead_return"] = float(_finite(frame, "futures_lead_return").median())
        result["median_liquidation_share_of_volume"] = float(
            _finite(frame, "liq_share_of_perp_volume").median()
        )
    return result


def _policy_pass(summary: dict[str, object], *, vwap_required: bool) -> dict[str, bool]:
    thirty = summary.get("reversal_30m") or summary.get("continuation_30m") or {}
    count = int(summary.get("count", 0))
    median = thirty.get("median") if isinstance(thirty, dict) else None
    hit = thirty.get("cost_after_hit_rate") if isinstance(thirty, dict) else None
    checks = {
        "independent_events_at_least_20": count >= 20,
        "median_30m_move_covers_round_trip_cost": (
            median is not None and float(median) >= ROUND_TRIP_COST_RATE
        ),
        "30m_cost_after_hit_rate_at_least_55pct": (
            hit is not None and float(hit) >= 0.55
        ),
    }
    if vwap_required:
        reclaim = summary.get("vwap_reclaim_60m_rate")
        checks["vwap_reclaim_60m_at_least_50pct"] = (
            reclaim is not None and float(reclaim) >= 0.50
        )
    return checks


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def run(cache: Path, output: Path) -> dict[str, object]:
    days = _months(START_MONTH, END_MONTH)
    requests = [(symbol, day) for day in days for symbol in SYMBOLS]
    obtained: dict[tuple[str, date], dict[str, Path]] = {}
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = {
            executor.submit(obtain_day, symbol, day, cache): (symbol, day)
            for symbol, day in requests
        }
        for future in as_completed(futures):
            key = futures[future]
            obtained[key] = future.result()

    panels: list[pd.DataFrame] = []
    coverage: dict[str, dict[str, object]] = {symbol: {} for symbol in SYMBOLS}
    for day in days:
        for symbol in SYMBOLS:
            panel = build_day(symbol, day, obtained[(symbol, day)])
            panels.append(panel)
            coverage[symbol][day.isoformat()] = {
                "minute_rows": int(len(panel)),
                "liquidation_minutes": int((panel["liq_total"] > 0.0).sum()),
                "derivative_ticker_ready_minutes": int(panel["open_interest"].notna().sum()),
            }
    panel = apply_causal_event_thresholds(pd.concat(panels, ignore_index=True))
    events = classify_and_score(panel)
    if events.empty:
        raise StudyError("no independent liquidation episodes survived causal screening")
    events = events.sort_values(["minute", "symbol"], kind="stable").reset_index(drop=True)
    events.to_csv(output / "events.csv", index=False)

    summaries: dict[str, object] = {
        "ALL_REVERSAL": _summary(events, "reversal"),
        "ALL_CONTINUATION": _summary(events, "continuation"),
    }
    for regime, group in events.groupby("regime", sort=True):
        policy = "continuation" if regime == "SPOT_CONFIRMED_INFORMATION_MOVE" else "reversal"
        summaries[regime] = _summary(group, policy)

    forced = events[
        events["regime"].isin(
            {"FORCED_BASIS_DISLOCATION", "FORCED_OI_DERIVATIVES_LEAD"},
        )
    ]
    information = events[events["regime"] == "SPOT_CONFIRMED_INFORMATION_MOVE"]
    forced_summary = _summary(forced, "reversal")
    information_summary = _summary(information, "continuation")
    forced_checks = _policy_pass(forced_summary, vwap_required=True)
    information_checks = _policy_pass(information_summary, vwap_required=False)
    promote_forced = all(forced_checks.values())
    promote_information = all(information_checks.values())

    if promote_forced and promote_information:
        decision = "PROMOTE_TWO_STATE_LIQUIDATION_ROUTER_TO_NAUTILUS_PROTOTYPE"
    elif promote_forced:
        decision = "PROMOTE_FORCED_DELEVERAGING_REVERSAL_ONLY_TO_NAUTILUS_PROTOTYPE"
    elif promote_information:
        decision = "PROMOTE_SPOT_CONFIRMED_CONTINUATION_ONLY_TO_NAUTILUS_PROTOTYPE"
    else:
        decision = "DISCARD_LIQUIDATION_VWAP_ROUTER; CONDITIONAL_PRICE_SPACE_DID_NOT_CLEAR_COSTS"

    result = {
        "schema": "candidate-16-v9-tardis-liquidation-study-v1",
        "role": "causal mechanism study; no fills, PnL, account, or NAV claim",
        "external_sources": {
            "liquidations_and_derivative_state": (
                "Tardis.dev normalized Binance Futures first-day monthly CSV samples"
            ),
            "spot_and_perpetual_bars": "checksum-verified Binance Vision daily 1m klines",
        },
        "sample_contract": {
            "start_month": START_MONTH,
            "end_month": END_MONTH,
            "calendar_days": len(days),
            "symbol_days": len(days) * len(SYMBOLS),
            "reason_for_first_day_sampling": (
                "Tardis historical first day of every month is openly downloadable "
                "without an API key"
            ),
            "post_stream_change_start": (
                "2021-09 avoids the known April-August 2021 reporting transition"
            ),
        },
        "symbols": list(SYMBOLS),
        "event_contract": {
            "threshold": "shifted expanding 99th percentile of prior nonzero symbol minutes",
            "minimum_prior_nonzero_minutes": MIN_PRIOR_NONZERO_LIQUIDATION_MINUTES,
            "dominance_min": DOMINANCE_MIN,
            "same_direction_within_symbol_decluster_minutes": WITHIN_SYMBOL_DECLUSTER_MINUTES,
            "cross_symbol_global_cluster_minutes": GLOBAL_CLUSTER_MINUTES,
            "must_move_with_liquidation": True,
            "must_be_outside_trailing_4h_vwap": True,
            "round_trip_cost_rate": ROUND_TRIP_COST_RATE,
        },
        "coverage": coverage,
        "candidate_minutes": int(panel["event_candidate"].sum()),
        "global_independent_events": int(len(events)),
        "regime_counts": {
            str(key): int(value)
            for key, value in events["regime"].value_counts().sort_index().items()
        },
        "summaries": summaries,
        "forced_deleveraging_reversal": {
            "summary": forced_summary,
            "checks": forced_checks,
            "promote": promote_forced,
        },
        "spot_confirmed_information_continuation": {
            "summary": information_summary,
            "checks": information_checks,
            "promote": promote_information,
        },
        "promote": promote_forced or promote_information,
        "decision": decision,
        "known_observation_limit": (
            "Binance forceOrder stream is a maximum one snapshot per second since "
            "2021-04-27, so liquidation notional is a lower bound"
        ),
    }
    write_json(output / "study.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    result = run(args.cache.resolve(), args.output.resolve())
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
