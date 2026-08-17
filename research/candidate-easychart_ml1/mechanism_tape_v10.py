#!/usr/bin/env python3
"""Exact aggregate-trade tape for mechanism-level decisions.

One-minute taker summaries cannot distinguish a forced burst, sustained informed
aggression, absorption, and a true flow reversal. This module reads Binance
USD-M aggregate trades with checksum verification and adds strictly pre-decision
features to the v9 action grammar. It processes one UTC day at a time to keep
memory bounded. No trade at or after the decision timestamp is visible.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import mechanism_harvest_v9 as v9
from mechanism_data_v2 import _read_zip_csv, _verified_archive

base = v9.base
SYMBOLS = base.SYMBOLS
VISION = "https://data.binance.vision/data/futures/um/daily/aggTrades"
WINDOWS = (5, 15, 30, 60, 180, 600)


def _window_feature_names(window: int) -> tuple[str, ...]:
    suffix = f"{window}s"
    return (
        f"tape_log_count_{suffix}",
        f"tape_log_quote_{suffix}",
        f"tape_aligned_delta_{suffix}",
        f"tape_aligned_move_bps_{suffix}",
        f"tape_range_bps_{suffix}",
        f"tape_efficiency_{suffix}",
        f"tape_aligned_large_delta_{suffix}",
        f"tape_large_share_{suffix}",
        f"tape_entropy_{suffix}",
        f"tape_aligned_run_fraction_{suffix}",
        f"tape_adverse_run_fraction_{suffix}",
        f"tape_burst_concentration_{suffix}",
        f"tape_interarrival_cv_{suffix}",
        f"tape_aligned_impact_per_million_{suffix}",
        f"tape_absorption_{suffix}",
        f"tape_flow_price_agreement_{suffix}",
    )


TAPE_FEATURE_COLUMNS = tuple(
    name
    for window in WINDOWS
    for name in _window_feature_names(window)
) + (
    "tape_available",
    "tape_delta_flip_15_vs_prior45",
    "tape_move_flip_15_vs_prior45",
    "tape_intensity_acceleration_15_vs_prior45",
    "tape_adverse_impact_decay",
    "tape_common_aligned_delta_30s",
    "tape_common_aligned_move_30s",
    "tape_aligned_delta_breadth_30s",
    "tape_aligned_move_breadth_30s",
    "tape_residual_aligned_delta_30s",
    "tape_residual_aligned_move_30s",
    "tape_common_intensity_30s",
    "tape_cross_asset_delta_dispersion_30s",
    "tape_cross_asset_move_dispersion_30s",
)
FEATURE_COLUMNS = tuple(v9.FEATURE_COLUMNS) + TAPE_FEATURE_COLUMNS


@dataclass(frozen=True)
class TapeWindow:
    count: int
    quote: float
    signed_quote: float
    delta: float
    move_bps: float
    range_bps: float
    efficiency: float
    large_share: float
    large_delta: float
    entropy: float
    buy_run_fraction: float
    sell_run_fraction: float
    burst_concentration: float
    interarrival_cv: float
    impact_per_million: float


@dataclass
class TapeStore:
    time_ns: np.ndarray
    price: np.ndarray
    quote: np.ndarray
    sign: np.ndarray

    @classmethod
    def empty(cls) -> "TapeStore":
        return cls(
            time_ns=np.empty(0, dtype=np.int64),
            price=np.empty(0, dtype=np.float64),
            quote=np.empty(0, dtype=np.float64),
            sign=np.empty(0, dtype=np.int8),
        )

    def between(self, start_ns: int, end_ns: int) -> tuple[int, int]:
        left = int(np.searchsorted(self.time_ns, start_ns, side="left"))
        right = int(np.searchsorted(self.time_ns, end_ns, side="left"))
        return left, right

    def summarize(self, end_ns: int, seconds: int) -> TapeWindow:
        start_ns = end_ns - int(seconds * 1_000_000_000)
        left, right = self.between(start_ns, end_ns)
        return self._summarize_slice(left, right)

    def segment(self, start_ns: int, end_ns: int) -> TapeWindow:
        left, right = self.between(start_ns, end_ns)
        return self._summarize_slice(left, right)

    def _summarize_slice(self, left: int, right: int) -> TapeWindow:
        count = right - left
        if count <= 0:
            return TapeWindow(
                count=0,
                quote=0.0,
                signed_quote=0.0,
                delta=np.nan,
                move_bps=np.nan,
                range_bps=np.nan,
                efficiency=np.nan,
                large_share=np.nan,
                large_delta=np.nan,
                entropy=np.nan,
                buy_run_fraction=np.nan,
                sell_run_fraction=np.nan,
                burst_concentration=np.nan,
                interarrival_cv=np.nan,
                impact_per_million=np.nan,
            )
        time_ns = self.time_ns[left:right]
        price = self.price[left:right]
        quote = self.quote[left:right]
        sign = self.sign[left:right]
        total_quote = float(quote.sum())
        signed_quote = float(np.dot(quote, sign.astype(np.float64)))
        delta = signed_quote / total_quote if total_quote > 0.0 else np.nan
        first_price = float(price[0])
        last_price = float(price[-1])
        if first_price > 0.0 and last_price > 0.0:
            move_bps = float(math.log(last_price / first_price) * 10_000.0)
            range_bps = float((float(price.max()) - float(price.min())) / first_price * 10_000.0)
            log_price = np.log(price)
            path = float(np.abs(np.diff(log_price)).sum())
            efficiency = abs(float(log_price[-1] - log_price[0])) / path if path > 0.0 else 0.0
        else:
            move_bps = range_bps = efficiency = np.nan

        threshold = float(np.quantile(quote, 0.90)) if count >= 10 else float(np.max(quote))
        large_mask = quote >= threshold
        large_quote = float(quote[large_mask].sum())
        large_signed = float(np.dot(quote[large_mask], sign[large_mask].astype(np.float64)))
        large_share = large_quote / total_quote if total_quote > 0.0 else np.nan
        large_delta = large_signed / large_quote if large_quote > 0.0 else np.nan

        buy_probability = float(np.mean(sign > 0))
        if buy_probability <= 0.0 or buy_probability >= 1.0:
            entropy = 0.0
        else:
            entropy = -(
                buy_probability * math.log(buy_probability)
                + (1.0 - buy_probability) * math.log(1.0 - buy_probability)
            ) / math.log(2.0)

        changes = np.flatnonzero(np.diff(sign) != 0) + 1
        starts = np.concatenate(([0], changes))
        ends = np.concatenate((changes, [count]))
        lengths = ends - starts
        run_signs = sign[starts]
        buy_run = int(lengths[run_signs > 0].max()) if np.any(run_signs > 0) else 0
        sell_run = int(lengths[run_signs < 0].max()) if np.any(run_signs < 0) else 0

        second = time_ns // 1_000_000_000
        _, inverse = np.unique(second, return_inverse=True)
        second_quote = np.bincount(inverse, weights=quote)
        burst = float(second_quote.max() / total_quote) if total_quote > 0.0 else np.nan

        if count >= 3:
            gaps = np.diff(time_ns).astype(np.float64) / 1_000_000_000.0
            mean_gap = float(gaps.mean())
            interarrival_cv = float(gaps.std() / mean_gap) if mean_gap > 0.0 else 0.0
        else:
            interarrival_cv = np.nan
        impact = move_bps / max(abs(signed_quote) / 1_000_000.0, 1e-6) if math.isfinite(move_bps) else np.nan
        impact = float(np.clip(impact, -500.0, 500.0)) if math.isfinite(impact) else np.nan
        return TapeWindow(
            count=count,
            quote=total_quote,
            signed_quote=signed_quote,
            delta=delta,
            move_bps=move_bps,
            range_bps=range_bps,
            efficiency=efficiency,
            large_share=large_share,
            large_delta=large_delta,
            entropy=entropy,
            buy_run_fraction=buy_run / count,
            sell_run_fraction=sell_run / count,
            burst_concentration=burst,
            interarrival_cv=interarrival_cv,
            impact_per_million=impact,
        )


def _timestamp_ns(values: pd.Series) -> np.ndarray:
    numeric = pd.to_numeric(values, errors="coerce")
    median = float(numeric.dropna().abs().median()) if numeric.notna().any() else 0.0
    unit = "us" if median > 1e14 else "ms" if median > 1e11 else "s"
    timestamp = pd.to_datetime(numeric, unit=unit, utc=True, errors="coerce")
    return timestamp.astype("int64").to_numpy()


def _buyer_maker_sign(values: pd.Series) -> np.ndarray:
    if values.dtype == bool:
        buyer_maker = values.to_numpy(bool)
    else:
        normalized = values.astype(str).str.strip().str.lower()
        buyer_maker = normalized.isin(("true", "1", "t", "yes")).to_numpy(bool)
    # buyer-is-maker means the aggressor sold.
    return np.where(buyer_maker, -1, 1).astype(np.int8)


def load_aggtrades_day(symbol: str, day: date, cache: Path) -> TapeStore:
    stamp = day.isoformat()
    name = f"{symbol}-aggTrades-{stamp}.zip"
    url = f"{VISION}/{symbol}/{name}"
    path = _verified_archive(url, cache / symbol / name)
    frame = _read_zip_csv(path)
    columns = {str(column).strip().lower(): column for column in frame.columns}
    if {"price", "quantity"}.issubset(columns):
        price_column = columns["price"]
        quantity_column = columns["quantity"]
        time_column = next(
            columns[name]
            for name in ("transact_time", "timestamp", "time")
            if name in columns
        )
        maker_column = next(
            columns[name]
            for name in ("is_buyer_maker", "buyer_maker")
            if name in columns
        )
    else:
        if frame.shape[1] < 7:
            raise RuntimeError(f"unexpected aggTrades schema in {path}: {frame.shape}")
        price_column = frame.columns[1]
        quantity_column = frame.columns[2]
        time_column = frame.columns[5]
        maker_column = frame.columns[6]
        first = str(frame.iloc[0][price_column]).strip().lower()
        if first in ("price", "p"):
            frame = frame.iloc[1:].copy()
    price = pd.to_numeric(frame[price_column], errors="coerce").to_numpy(float)
    quantity = pd.to_numeric(frame[quantity_column], errors="coerce").to_numpy(float)
    time_ns = _timestamp_ns(frame[time_column])
    sign = _buyer_maker_sign(frame[maker_column])
    valid = (
        np.isfinite(price)
        & np.isfinite(quantity)
        & (price > 0.0)
        & (quantity > 0.0)
        & (time_ns > 0)
    )
    if not np.any(valid):
        return TapeStore.empty()
    time_ns = time_ns[valid]
    price = price[valid]
    quote = price * quantity[valid]
    sign = sign[valid]
    order = np.argsort(time_ns, kind="stable")
    return TapeStore(
        time_ns=time_ns[order].astype(np.int64, copy=False),
        price=price[order].astype(np.float64, copy=False),
        quote=quote[order].astype(np.float64, copy=False),
        sign=sign[order].astype(np.int8, copy=False),
    )


def _combine_day_with_tail(previous: TapeStore, current: TapeStore, day: date) -> TapeStore:
    day_start = pd.Timestamp(day, tz="UTC").value
    tail_start = day_start - 11 * 60 * 1_000_000_000
    left = int(np.searchsorted(previous.time_ns, tail_start, side="left"))
    arrays = []
    for old, new in (
        (previous.time_ns[left:], current.time_ns),
        (previous.price[left:], current.price),
        (previous.quote[left:], current.quote),
        (previous.sign[left:], current.sign),
    ):
        arrays.append(np.concatenate((old, new)))
    return TapeStore(
        time_ns=arrays[0].astype(np.int64, copy=False),
        price=arrays[1].astype(np.float64, copy=False),
        quote=arrays[2].astype(np.float64, copy=False),
        sign=arrays[3].astype(np.int8, copy=False),
    )


def _aligned_window_features(window: TapeWindow, seconds: int, side: int) -> dict[str, float]:
    suffix = f"{seconds}s"
    aligned_delta = side * window.delta if math.isfinite(window.delta) else np.nan
    aligned_move = side * window.move_bps if math.isfinite(window.move_bps) else np.nan
    aligned_large = side * window.large_delta if math.isfinite(window.large_delta) else np.nan
    aligned_run = window.buy_run_fraction if side > 0 else window.sell_run_fraction
    adverse_run = window.sell_run_fraction if side > 0 else window.buy_run_fraction
    adverse_flow = max(0.0, -aligned_delta) if math.isfinite(aligned_delta) else np.nan
    adverse_progress = max(0.0, -aligned_move) if math.isfinite(aligned_move) else np.nan
    if math.isfinite(adverse_flow) and math.isfinite(adverse_progress) and math.isfinite(window.range_bps):
        absorption = adverse_flow * max(0.0, 1.0 - adverse_progress / max(window.range_bps, 0.25))
    else:
        absorption = np.nan
    agreement = aligned_delta * aligned_move if math.isfinite(aligned_delta) and math.isfinite(aligned_move) else np.nan
    aligned_impact = side * window.impact_per_million if math.isfinite(window.impact_per_million) else np.nan
    return {
        f"tape_log_count_{suffix}": math.log1p(window.count),
        f"tape_log_quote_{suffix}": math.log1p(window.quote),
        f"tape_aligned_delta_{suffix}": aligned_delta,
        f"tape_aligned_move_bps_{suffix}": aligned_move,
        f"tape_range_bps_{suffix}": window.range_bps,
        f"tape_efficiency_{suffix}": window.efficiency,
        f"tape_aligned_large_delta_{suffix}": aligned_large,
        f"tape_large_share_{suffix}": window.large_share,
        f"tape_entropy_{suffix}": window.entropy,
        f"tape_aligned_run_fraction_{suffix}": aligned_run,
        f"tape_adverse_run_fraction_{suffix}": adverse_run,
        f"tape_burst_concentration_{suffix}": window.burst_concentration,
        f"tape_interarrival_cv_{suffix}": window.interarrival_cv,
        f"tape_aligned_impact_per_million_{suffix}": aligned_impact,
        f"tape_absorption_{suffix}": absorption,
        f"tape_flow_price_agreement_{suffix}": agreement,
    }


def _decision_features(
    symbol: str,
    timestamp: pd.Timestamp,
    side: int,
    stores: dict[str, TapeStore],
) -> dict[str, Any]:
    end_ns = timestamp.value
    store = stores.get(symbol, TapeStore.empty())
    windows = {seconds: store.summarize(end_ns, seconds) for seconds in WINDOWS}
    values: dict[str, Any] = {
        "symbol": symbol,
        "snapshot_time": timestamp,
        "side": side,
        "tape_available": float(windows[60].count > 0),
    }
    for seconds, summary in windows.items():
        values.update(_aligned_window_features(summary, seconds, side))

    prior45 = store.segment(end_ns - 60_000_000_000, end_ns - 15_000_000_000)
    late15 = windows[15]
    aligned_late_delta = side * late15.delta if math.isfinite(late15.delta) else np.nan
    aligned_prior_delta = side * prior45.delta if math.isfinite(prior45.delta) else np.nan
    aligned_late_move = side * late15.move_bps if math.isfinite(late15.move_bps) else np.nan
    aligned_prior_move = side * prior45.move_bps if math.isfinite(prior45.move_bps) else np.nan
    values["tape_delta_flip_15_vs_prior45"] = (
        aligned_late_delta - aligned_prior_delta
        if math.isfinite(aligned_late_delta) and math.isfinite(aligned_prior_delta)
        else np.nan
    )
    values["tape_move_flip_15_vs_prior45"] = (
        aligned_late_move - aligned_prior_move
        if math.isfinite(aligned_late_move) and math.isfinite(aligned_prior_move)
        else np.nan
    )
    late_rate = late15.quote / 15.0
    prior_rate = prior45.quote / 45.0
    values["tape_intensity_acceleration_15_vs_prior45"] = math.log(
        (late_rate + 1.0) / (prior_rate + 1.0)
    )
    prior_adverse_flow = max(0.0, -aligned_prior_delta) if math.isfinite(aligned_prior_delta) else np.nan
    prior_adverse_move = max(0.0, -aligned_prior_move) if math.isfinite(aligned_prior_move) else np.nan
    late_adverse_flow = max(0.0, -aligned_late_delta) if math.isfinite(aligned_late_delta) else np.nan
    late_adverse_move = max(0.0, -aligned_late_move) if math.isfinite(aligned_late_move) else np.nan
    if all(math.isfinite(value) for value in (prior_adverse_flow, prior_adverse_move, late_adverse_flow, late_adverse_move)):
        prior_impact = prior_adverse_move / max(prior_adverse_flow, 1e-4)
        late_impact = late_adverse_move / max(late_adverse_flow, 1e-4)
        values["tape_adverse_impact_decay"] = prior_impact - late_impact
    else:
        values["tape_adverse_impact_decay"] = np.nan

    cross: list[tuple[float, float, float]] = []
    for cross_store in stores.values():
        summary = cross_store.summarize(end_ns, 30)
        if math.isfinite(summary.delta) and math.isfinite(summary.move_bps):
            cross.append((summary.delta, summary.move_bps, summary.quote / 30.0))
    if cross:
        cross_array = np.asarray(cross, dtype=float)
        common_delta = float(np.median(cross_array[:, 0]))
        common_move = float(np.median(cross_array[:, 1]))
        asset_delta = windows[30].delta
        asset_move = windows[30].move_bps
        values["tape_common_aligned_delta_30s"] = side * common_delta
        values["tape_common_aligned_move_30s"] = side * common_move
        values["tape_aligned_delta_breadth_30s"] = float(np.mean(side * cross_array[:, 0] > 0.0))
        values["tape_aligned_move_breadth_30s"] = float(np.mean(side * cross_array[:, 1] > 0.0))
        values["tape_residual_aligned_delta_30s"] = side * (asset_delta - common_delta) if math.isfinite(asset_delta) else np.nan
        values["tape_residual_aligned_move_30s"] = side * (asset_move - common_move) if math.isfinite(asset_move) else np.nan
        values["tape_common_intensity_30s"] = math.log1p(float(np.median(cross_array[:, 2])))
        values["tape_cross_asset_delta_dispersion_30s"] = float(np.std(cross_array[:, 0]))
        values["tape_cross_asset_move_dispersion_30s"] = float(np.std(cross_array[:, 1]))
    else:
        for column in TAPE_FEATURE_COLUMNS[-8:]:
            values[column] = np.nan
    return values


def _load_day_stores(day: date, cache: Path) -> dict[str, TapeStore]:
    stores: dict[str, TapeStore] = {}
    previous_day = day - timedelta(days=1)
    for symbol in SYMBOLS:
        previous = load_aggtrades_day(symbol, previous_day, cache)
        current = load_aggtrades_day(symbol, day, cache)
        stores[symbol] = _combine_day_with_tail(previous, current, day)
    return stores


def augment_actions(actions: pd.DataFrame, cache: Path) -> pd.DataFrame:
    if actions.empty:
        for column in TAPE_FEATURE_COLUMNS:
            actions[column] = np.nan
        return actions
    output = actions.copy()
    output["snapshot_time"] = pd.to_datetime(output["snapshot_time"], utc=True, errors="raise")
    output["decision_day"] = output["snapshot_time"].dt.date
    unique = output[["symbol", "snapshot_time", "side", "decision_day"]].drop_duplicates()
    feature_rows: list[dict[str, Any]] = []
    for day, group in unique.groupby("decision_day", sort=True):
        stores = _load_day_stores(day, cache)
        for row in group.itertuples(index=False):
            feature_rows.append(
                _decision_features(
                    symbol=str(row.symbol),
                    timestamp=pd.Timestamp(row.snapshot_time),
                    side=int(row.side),
                    stores=stores,
                )
            )
        del stores
    features = pd.DataFrame(feature_rows)
    output = output.merge(
        features,
        on=["symbol", "snapshot_time", "side"],
        how="left",
        validate="many_to_one",
    )
    output = output.drop(columns=["decision_day"])
    missing = sorted(set(TAPE_FEATURE_COLUMNS) - set(output.columns))
    if missing:
        raise RuntimeError(f"missing tape features after merge: {missing}")
    return output


def harvest(period: str, start: date, end: date, cache: Path, output: Path) -> None:
    v9._install()
    base.FEATURE_COLUMNS = FEATURE_COLUMNS
    output.mkdir(parents=True, exist_ok=True)
    base.harvest(period, start, end, cache / "bars", output)
    path = output / "actions.csv"
    actions = pd.read_csv(path, low_memory=False)
    augmented = augment_actions(actions, cache / "aggtrades")
    augmented.to_csv(path, index=False)
    diagnostics_path = output / "diagnostics.json"
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    diagnostics["features"] = list(FEATURE_COLUMNS)
    diagnostics["exact_tape"] = {
        "source": "CHECKSUM_VERIFIED_BINANCE_USDM_AGGTRADES",
        "availability": "STRICTLY_BEFORE_SNAPSHOT_TIME",
        "windows_seconds": list(WINDOWS),
        "actions_with_60s_tape": int(pd.to_numeric(augmented["tape_available"], errors="coerce").fillna(0.0).sum()),
    }
    diagnostics_path.write_text(json.dumps(diagnostics, indent=2, sort_keys=True), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--cache", type=Path, default=Path(".cache/mechanism-v10"))
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    harvest(args.period, args.start, args.end, args.cache, args.output)


if __name__ == "__main__":
    main()
