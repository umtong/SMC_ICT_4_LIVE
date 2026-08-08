#!/usr/bin/env python3
"""Candidate 15 V24 exact Binance aggTrades quarter-hour mechanism screen.

Only BTC is used for this high-information screening stage because official
historical one-second futures klines are unavailable. Official daily aggTrades
archives are streamed in chunks, and only the opening minute of each
quarter-hour is retained. Seconds 0-9 define the state; seconds 10-59 define an
independent transition; entry occurs at the last trade of the event minute.

A fixed route that survives all declared splits is expanded unchanged to the
four-asset universe before any NautilusTrader promotion.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import numpy as np
import pandas as pd

import diagnose_v23_quarter_hour_10s as base


BTC = "BTCUSDT"
IMMEDIATE = "QH_10S_IMMEDIATE_CONFIRMATION_4H"
SHORT_REVERSAL = "QH_10S_SHORT_REVERSAL_MEDIUM_DELIVERY_8H"
AGG_COLUMNS = (
    "agg_trade_id",
    "price",
    "quantity",
    "first_trade_id",
    "last_trade_id",
    "transact_time",
    "is_buyer_maker",
)


def selected_event_dates(protocol: dict[str, Any]) -> list[date]:
    data = protocol["data"]
    values: set[date] = set()
    for key in (
        "development_dates",
        "stability_dates",
        "july_confirmation_dates",
        "latest_august_dates",
    ):
        values.update(date.fromisoformat(value) for value in data[key])
    return sorted(values)


def _timestamp_milliseconds(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    valid = numeric.dropna()
    if valid.empty:
        return pd.Series(np.nan, index=values.index)
    first = float(valid.iloc[0])
    if 1e15 <= first < 1e17:
        numeric = np.floor(numeric / 1000.0)
    elif not (1e12 <= first < 1e14):
        raise RuntimeError(f"unsupported aggTrades timestamp magnitude {first}")
    return numeric


def _stream_selected_trades(path: Path) -> pd.DataFrame:
    selected: list[pd.DataFrame] = []
    with ZipFile(path) as archive:
        members = [name for name in archive.namelist() if not name.endswith("/")]
        if len(members) != 1:
            raise RuntimeError(f"unexpected aggTrades members in {path}: {members}")
        with archive.open(members[0]) as stream:
            chunks = pd.read_csv(
                stream,
                header=None,
                names=AGG_COLUMNS,
                chunksize=500_000,
                low_memory=False,
            )
            for chunk in chunks:
                timestamp_ms = _timestamp_milliseconds(chunk["transact_time"])
                price = pd.to_numeric(chunk["price"], errors="coerce")
                quantity = pd.to_numeric(chunk["quantity"], errors="coerce")
                first_id = pd.to_numeric(chunk["first_trade_id"], errors="coerce")
                last_id = pd.to_numeric(chunk["last_trade_id"], errors="coerce")
                valid = (
                    timestamp_ms.notna()
                    & price.notna()
                    & quantity.notna()
                    & (price > 0.0)
                    & (quantity > 0.0)
                )
                if not valid.any():
                    continue
                timestamp_ms = timestamp_ms[valid].astype("int64")
                minute = (timestamp_ms // 60_000) % 60
                second = (timestamp_ms // 1_000) % 60
                mask = (minute % 15 == 0) & (second < 60)
                if not mask.any():
                    continue
                index = timestamp_ms.index[mask]
                maker_text = (
                    chunk.loc[index, "is_buyer_maker"]
                    .astype(str)
                    .str.strip()
                    .str.lower()
                )
                buyer_maker = maker_text.isin(("true", "1", "t"))
                subset = pd.DataFrame(
                    {
                        "timestamp_ms": timestamp_ms.loc[index].astype("int64"),
                        "price": price.loc[index].astype(float),
                        "quantity": quantity.loc[index].astype(float),
                        "first_trade_id": first_id.loc[index].fillna(0).astype("int64"),
                        "last_trade_id": last_id.loc[index].fillna(0).astype("int64"),
                        "taker_buy": (~buyer_maker).to_numpy(dtype=bool),
                    }
                )
                selected.append(subset)
    if not selected:
        raise RuntimeError(f"no quarter-hour opening trades in {path}")
    output = pd.concat(selected, ignore_index=True)
    return output.sort_values("timestamp_ms", kind="stable")


def _aggregate_seconds(trades: pd.DataFrame) -> pd.DataFrame:
    trades = trades.copy()
    trades["second_ms"] = (trades["timestamp_ms"] // 1000) * 1000
    trades["quote_volume"] = trades["price"] * trades["quantity"]
    trades["taker_buy_volume"] = np.where(
        trades["taker_buy"], trades["quantity"], 0.0
    )
    trades["taker_buy_quote_volume"] = np.where(
        trades["taker_buy"], trades["quote_volume"], 0.0
    )
    trade_count = (
        trades["last_trade_id"] - trades["first_trade_id"] + 1
    ).clip(lower=1)
    trades["trade_count"] = trade_count
    grouped = trades.groupby("second_ms", sort=True)
    output = grouped.agg(
        open=("price", "first"),
        high=("price", "max"),
        low=("price", "min"),
        close=("price", "last"),
        volume=("quantity", "sum"),
        quote_volume=("quote_volume", "sum"),
        trades=("trade_count", "sum"),
        taker_buy_volume=("taker_buy_volume", "sum"),
        taker_buy_quote_volume=("taker_buy_quote_volume", "sum"),
    )
    open_time = pd.to_datetime(output.index.astype("int64"), unit="ms", utc=True)
    output["open_time"] = open_time.to_numpy()
    output.index = open_time + pd.Timedelta(seconds=1)
    return output


def load_daily_aggtrade_seconds(
    symbol: str,
    event_dates: list[date],
    data_dir: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    manifest: list[dict[str, Any]] = []
    for day in event_dates:
        filename = f"{symbol}-aggTrades-{day.isoformat()}.zip"
        url = (
            "https://data.binance.vision/data/futures/um/daily/aggTrades/"
            f"{symbol}/{filename}"
        )
        path = data_dir / "aggTrades" / symbol / filename
        record = base.download(url, path)
        raw = _stream_selected_trades(path)
        second = _aggregate_seconds(raw)
        frames.append(second)
        record.update(
            {
                "symbol": symbol,
                "interval": "aggTrades-quarter-hour-minute",
                "token": day.isoformat(),
                "selected_trades": len(raw.index),
                "selected_seconds": len(second.index),
            }
        )
        manifest.append(record)
    output = pd.concat(frames).sort_index(kind="stable")
    output = output[~output.index.duplicated(keep="last")]
    return output, manifest


def boundary_events(
    symbol: str,
    seconds_frame: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    open_stamp = pd.DatetimeIndex(seconds_frame["open_time"])
    qh = open_stamp.minute % 15 == 0
    selected = seconds_frame.loc[qh].copy()
    selected["event_ts"] = pd.DatetimeIndex(
        selected["open_time"]
    ).floor("15min").to_numpy()
    selected["second"] = pd.DatetimeIndex(selected["open_time"]).second

    first = selected[selected["second"] < 10].copy()
    later = selected[selected["second"] >= 10].copy()
    first_group = first.groupby("event_ts", sort=True).agg(
        ten_second_quote_volume=("quote_volume", "sum"),
        ten_second_taker_buy_quote=("taker_buy_quote_volume", "sum"),
        ten_second_trade_count=("trades", "sum"),
        ten_second_open=("open", "first"),
        ten_second_high=("high", "max"),
        ten_second_low=("low", "min"),
        ten_second_close=("close", "last"),
        first_observed_seconds=("second", "nunique"),
    )
    later_group = later.groupby("event_ts", sort=True).agg(
        confirmation_quote_volume=("quote_volume", "sum"),
        confirmation_taker_buy_quote=("taker_buy_quote_volume", "sum"),
        confirmation_trade_count=("trades", "sum"),
        confirmation_open=("open", "first"),
        confirmation_high=("high", "max"),
        confirmation_low=("low", "min"),
        confirmation_close=("close", "last"),
        confirmation_observed_seconds=("second", "nunique"),
    )
    output = first_group.join(later_group, how="inner")
    output["symbol"] = symbol
    output["ten_second_imbalance"] = (
        2.0
        * output["ten_second_taker_buy_quote"]
        / output["ten_second_quote_volume"].replace(0.0, np.nan)
        - 1.0
    ).clip(-1.0, 1.0)
    output["confirmation_pressure"] = (
        2.0
        * output["confirmation_taker_buy_quote"]
        / output["confirmation_quote_volume"].replace(0.0, np.nan)
        - 1.0
    ).clip(-1.0, 1.0)
    output["confirmation_return"] = (
        output["confirmation_close"] / output["confirmation_open"] - 1.0
    )
    output["confirmation_body_fraction"] = (
        (output["confirmation_close"] - output["confirmation_open"]).abs()
        / (
            output["confirmation_high"] - output["confirmation_low"]
        ).replace(0.0, np.nan)
    )
    output["entry_ts"] = pd.DatetimeIndex(output.index) + pd.Timedelta(minutes=1)
    output["entry_price"] = output["confirmation_close"]
    output["event_minute_high"] = pd.concat(
        [output["ten_second_high"], output["confirmation_high"]], axis=1
    ).max(axis=1)
    output["event_minute_low"] = pd.concat(
        [output["ten_second_low"], output["confirmation_low"]], axis=1
    ).min(axis=1)
    output = output[
        (
            output["first_observed_seconds"]
            >= int(rules["minimum_first_window_observed_seconds"])
        )
        & (
            output["confirmation_observed_seconds"]
            >= int(rules["minimum_confirmation_observed_seconds"])
        )
        & (
            output["ten_second_quote_volume"]
            >= float(rules["minimum_ten_second_quote_volume"])
        )
    ]
    return output.replace([np.inf, -np.inf], np.nan).dropna(
        subset=[
            "ten_second_imbalance",
            "confirmation_pressure",
            "confirmation_return",
            "confirmation_body_fraction",
            "entry_price",
        ]
    )


def add_event_context(
    events: pd.DataFrame,
    minute_features: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    if events.empty:
        return events.copy()
    event_index = pd.DatetimeIndex(events.index)
    context = minute_features.reindex(event_index, method="ffill").copy()
    context.index = event_index
    output = events.join(context, how="left", rsuffix="_context")
    entry_index = pd.DatetimeIndex(pd.to_datetime(output["entry_ts"], utc=True))
    output["target_240m"] = (
        minute_features["target_240m"].reindex(entry_index).to_numpy()
    )
    output["target_480m"] = (
        minute_features["target_480m"].reindex(entry_index).to_numpy()
    )
    output["entry_reference_close"] = (
        minute_features["close"].reindex(entry_index).to_numpy()
    )
    output["entry_price_difference_bps"] = (
        output["entry_price"] / output["entry_reference_close"] - 1.0
    ) * 10_000.0
    output["outcome_origin"] = "EVENT_MINUTE_FINAL_TRADE"
    return output


def add_prior_boundary_state(
    frame: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    output = frame.sort_index(kind="stable").copy()
    imbalance = output["ten_second_imbalance"].astype(float)
    output["imbalance_threshold"] = (
        imbalance.abs()
        .shift(1)
        .rolling(
            int(rules["absolute_imbalance_prior_lookback_events"]),
            min_periods=int(rules["absolute_imbalance_prior_minimum_events"]),
        )
        .quantile(float(rules["absolute_imbalance_prior_quantile"]))
    )
    output["confirmation_abs_return_threshold"] = (
        output["confirmation_return"].abs()
        .shift(1)
        .rolling(
            int(rules["confirmation_abs_return_prior_lookback_events"]),
            min_periods=int(
                rules["confirmation_abs_return_prior_minimum_events"]
            ),
        )
        .quantile(float(rules["confirmation_abs_return_prior_quantile"]))
    )
    lag_count = int(rules["recent_quarter_hour_lag_events"])
    lag_values = pd.concat(
        [imbalance.shift(lag) for lag in range(1, lag_count + 1)],
        axis=1,
    )
    current_sign = np.sign(imbalance).replace(0.0, np.nan)
    output["same_phase_direction_agreement"] = np.sign(lag_values).eq(
        current_sign, axis=0
    ).mean(axis=1)
    output["fresh_phase_direction_agreement"] = output[
        "same_phase_direction_agreement"
    ]
    timestamps = pd.Series(pd.DatetimeIndex(output.index), index=output.index)
    span = timestamps - timestamps.shift(lag_count)
    fresh = span.le(
        pd.Timedelta(
            minutes=int(rules["recent_quarter_hour_maximum_span_minutes"])
        )
    )
    output.loc[~fresh, "same_phase_direction_agreement"] = np.nan
    output.loc[~fresh, "fresh_phase_direction_agreement"] = np.nan
    output["recent_flow_span_minutes"] = span.dt.total_seconds() / 60.0
    output["phase_minute"] = pd.DatetimeIndex(output.index).minute
    return output


def public_state_direction(
    frame: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.Series:
    columns = [
        f"ret_{minutes}m"
        for minutes in rules["public_return_windows_minutes"]
    ]
    returns = frame[columns].astype(float)
    positive = (returns > 0.0).mean(axis=1)
    negative = (returns < 0.0).mean(axis=1)
    minimum = float(rules["public_minimum_directional_return_agreement"])
    direction = pd.Series(0.0, index=frame.index)
    direction[positive >= minimum] = 1.0
    direction[negative >= minimum] = -1.0
    direction[
        frame["public_volume_ratio"]
        < float(rules["public_volume_ratio_minimum"])
    ] = 0.0
    return direction


def candidate_rows(
    symbol: str,
    frame: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    direction = np.sign(frame["ten_second_imbalance"].astype(float))
    public_direction = public_state_direction(frame, rules)
    extreme = (
        frame["ten_second_imbalance"].abs()
        >= frame["imbalance_threshold"]
    )
    entry_aligned = frame["entry_price_difference_bps"].abs() <= 5.0
    immediate = (
        extreme
        & entry_aligned
        & (direction * frame["confirmation_return"] > 0.0)
        & (direction * frame["confirmation_pressure"] > 0.0)
        & (
            frame["confirmation_body_fraction"]
            >= float(
                rules["immediate_confirmation_body_fraction_minimum"]
            )
        )
    )
    short_reversal = (
        extreme
        & entry_aligned
        & (direction * frame["confirmation_return"] < 0.0)
        & (
            frame["confirmation_return"].abs()
            >= frame["confirmation_abs_return_threshold"]
        )
        & (public_direction == direction)
        & (
            frame["same_phase_direction_agreement"]
            >= float(rules["recent_flow_minimum_direction_agreement"])
        )
    )

    rows: list[dict[str, Any]] = []
    for route, mask, target, horizon, priority in (
        (
            IMMEDIATE,
            immediate,
            "target_240m",
            int(rules["immediate_horizon_minutes"]),
            1,
        ),
        (
            SHORT_REVERSAL,
            short_reversal,
            "target_480m",
            int(rules["short_reversal_horizon_minutes"]),
            2,
        ),
    ):
        eligible = frame[mask & frame[target].notna()]
        for timestamp, item in eligible.iterrows():
            sign_value = float(np.sign(item["ten_second_imbalance"]))
            gross = sign_value * float(item[target])
            normalized = abs(float(item["ten_second_imbalance"])) / max(
                float(item["imbalance_threshold"]), 1e-12
            )
            quality = (
                abs(float(item["confirmation_pressure"]))
                + abs(float(item["confirmation_return"])) * 100.0
            )
            rows.append(
                {
                    "event_ts": pd.Timestamp(timestamp),
                    "entry_ts": pd.Timestamp(item["entry_ts"]),
                    "exit_ts": pd.Timestamp(item["entry_ts"])
                    + pd.Timedelta(minutes=horizon),
                    "symbol": symbol,
                    "route": route,
                    "direction": "LONG" if sign_value > 0.0 else "SHORT",
                    "horizon_minutes": horizon,
                    "route_priority": priority,
                    "ten_second_imbalance": float(
                        item["ten_second_imbalance"]
                    ),
                    "imbalance_threshold": float(
                        item["imbalance_threshold"]
                    ),
                    "confirmation_return": float(
                        item["confirmation_return"]
                    ),
                    "confirmation_pressure": float(
                        item["confirmation_pressure"]
                    ),
                    "confirmation_body_fraction": float(
                        item["confirmation_body_fraction"]
                    ),
                    "recent_flow_direction_agreement": float(
                        item["same_phase_direction_agreement"]
                    ),
                    "public_state_direction": float(
                        public_direction.loc[timestamp]
                    ),
                    "entry_price": float(item["entry_price"]),
                    "event_minute_high": float(item["event_minute_high"]),
                    "event_minute_low": float(item["event_minute_low"]),
                    "outcome_origin": "EVENT_MINUTE_FINAL_TRADE",
                    "rank_value": float(priority + normalized * (1.0 + quality)),
                    "gross_return": gross,
                }
            )
    return pd.DataFrame(rows)


base.SYMBOLS = (BTC,)
base.ROUTE_PERSISTENCE = IMMEDIATE
base.ROUTE_PUBLIC = SHORT_REVERSAL
base.selected_event_dates = selected_event_dates
base.load_daily_one_second = load_daily_aggtrade_seconds
base.boundary_events = boundary_events
base.add_event_context = add_event_context
base.add_prior_boundary_state = add_prior_boundary_state
base.candidate_rows = candidate_rows


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = base.load_object(protocol_path)
    if protocol["schema"] != "candidate-15-v24-quarter-hour-aggtrades-btc-screen-v1":
        raise RuntimeError("unexpected V24 protocol")
    summary = base.execute(protocol_path, output)
    passed = bool(summary.get("advance_to_nautilus"))
    summary["schema"] = "candidate-15-v24-summary-v1"
    summary["screen_scope"] = "BTC_ONLY_EXACT_AGGTRADES"
    summary["advance_to_four_asset_expansion"] = passed
    summary["advance_to_nautilus"] = False
    summary["classification"] = (
        "V24_EXACT_AGGTRADES_ROUTE_ADVANCE_TO_FOUR_ASSET"
        if passed
        else "V24_EXACT_AGGTRADES_ROUTER_REJECTED_OR_UNDERPOWERED"
    )
    summary["decision"] = (
        "Expand the fixed surviving route unchanged to BTC, ETH, SOL and XRP."
        if passed
        else "Do not tune this exact-data family; move to another independent mechanism."
    )
    base.write_json(output / "summary.json", summary)
    result = (output / "RESULT.md").read_text(encoding="utf-8")
    result = result.replace(
        "# Candidate 15 V23 — Exact ten-second quarter-hour order-imbalance diagnostic",
        "# Candidate 15 V24 — Exact aggTrades quarter-hour BTC mechanism screen",
    )
    result = result.replace(
        "**V23_QUARTER_HOUR_10S_ROUTE_ADVANCE_TO_NAUTILUS**",
        "**V24_EXACT_AGGTRADES_ROUTE_ADVANCE_TO_FOUR_ASSET**",
    )
    result = result.replace(
        "**V23_QUARTER_HOUR_10S_ROUTER_REJECTED_OR_UNDERPOWERED**",
        "**V24_EXACT_AGGTRADES_ROUTER_REJECTED_OR_UNDERPOWERED**",
    )
    result += (
        "\n## Exact-data implementation\n"
        "- state source: official Binance USD-M daily aggTrades\n"
        "- seconds 0-9: volume-normalized signed taker imbalance\n"
        "- seconds 10-59: independent transition\n"
        "- outcome origin: final trade of the event minute\n"
        "- scope: BTC-only high-information screen; no final success claim\n"
    )
    (output / "RESULT.md").write_text(result, encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    execute(args.protocol.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
