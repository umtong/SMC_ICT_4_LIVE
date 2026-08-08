#!/usr/bin/env python3
"""Candidate 15 V18 spot-versus-perpetual leadership diagnostic.

This is an economic mechanism screen.  It does not synthesize portfolio NAV or
replace NautilusTrader.  Event, state, confirmation and outcome use distinct
completed five-minute observations.  The latest July 2026 interval is declared
before execution and is not used to alter the fixed rules.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from hashlib import sha256
import json
import math
from pathlib import Path
import time
from typing import Any
from urllib.request import Request, urlopen
from zipfile import ZipFile

import numpy as np
import pandas as pd

import diagnose_v16_index_basis as common
import diagnose_v17_open_interest as oi_common
import run_v17_open_interest as metrics_adapter

SPOT_CONTINUATION = "SPOT_LED_POSITION_BUILDUP_CONTINUATION"
PERP_REVERSAL = "PERP_LED_DELEVERAGING_NONCONFIRMATION_REVERSAL"


def spot_archive_url(symbol: str, interval: str, token: str) -> str:
    return (
        "https://data.binance.vision/data/spot/monthly/klines/"
        f"{symbol}/{interval}/{symbol}-{interval}-{token}.zip"
    )


def download_spot(task: tuple[str, str, str, Path]) -> dict[str, Any]:
    symbol, interval, token, destination = task
    url = spot_archive_url(symbol, interval, token)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.stat().st_size < 100:
        request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-15-v18"})
        last: Exception | None = None
        for attempt in range(5):
            try:
                with urlopen(request, timeout=90) as response:  # noqa: S310 fixed host
                    payload = response.read()
                if len(payload) < 100:
                    raise RuntimeError(f"small response from {url}")
                temporary = destination.with_suffix(".zip.tmp")
                temporary.write_bytes(payload)
                with ZipFile(temporary) as archive:
                    if archive.testzip() is not None:
                        raise RuntimeError("corrupt spot archive")
                temporary.replace(destination)
                break
            except Exception as exc:
                last = exc
                if attempt == 4:
                    raise RuntimeError(f"download failed {url}: {last}") from exc
                time.sleep(2**attempt)
    payload = destination.read_bytes()
    return {
        "dataset": "spot_klines",
        "symbol": symbol,
        "month": token,
        "url": url,
        "path": str(destination),
        "bytes": len(payload),
        "sha256": sha256(payload).hexdigest(),
    }


def load_market_series(
    paths: list[Path],
    start: date,
    end: date,
) -> pd.DataFrame:
    if not paths:
        raise RuntimeError("no market archives")
    raw = pd.concat([common.read_zip(path) for path in paths], ignore_index=True)
    raw = raw.drop_duplicates("open_time", keep="last").sort_values("open_time")
    timestamps = pd.to_numeric(raw["open_time"], errors="raise").astype("int64")
    first = int(timestamps.iloc[0])
    if 1_000_000_000_000 <= first < 10_000_000_000_000:
        unit = "ms"
    elif 1_000_000_000_000_000 <= first < 10_000_000_000_000_000:
        unit = "us"
    else:
        raise RuntimeError(f"unsupported market timestamp magnitude {first}")
    index = pd.to_datetime(timestamps, unit=unit, utc=True) + pd.Timedelta(minutes=5)
    output = pd.DataFrame(index=index)
    for column in ("close", "quote_volume", "taker_buy_quote_volume"):
        output[column] = pd.to_numeric(raw[column], errors="coerce").to_numpy()
    output = output.dropna()
    output = output[~output.index.duplicated(keep="last")].sort_index()
    lower, upper = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    output = output[(output.index > lower) & (output.index <= upper)]
    expected = int((upper - lower).total_seconds() // 300)
    coverage = len(output.index) / max(expected, 1)
    if coverage < 0.995:
        raise RuntimeError(
            f"insufficient market coverage: {len(output.index)}/{expected} "
            f"({coverage:.6f})",
        )
    return output


def safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0.0, np.nan)


def symbol_state_events(
    symbol: str,
    spot: pd.DataFrame,
    futures: pd.DataFrame,
    index_price: pd.DataFrame,
    metrics: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    joined = (
        futures.rename(
            columns={
                "close": "futures_close",
                "quote_volume": "futures_quote_volume",
                "taker_buy_quote_volume": "futures_taker_buy_quote",
            }
        )
        .join(
            spot.rename(
                columns={
                    "close": "spot_close",
                    "quote_volume": "spot_quote_volume",
                    "taker_buy_quote_volume": "spot_taker_buy_quote",
                }
            ),
            how="inner",
        )
        .join(index_price.rename(columns={"close": "index_close"}), how="inner")
        .join(metrics, how="inner")
    )
    futures_close = joined["futures_close"].astype(float)
    spot_close = joined["spot_close"].astype(float)
    index_close = joined["index_close"].astype(float)
    futures_return = futures_close.pct_change()
    spot_return = spot_close.pct_change()
    index_return = index_close.pct_change()
    relative_return = futures_return - spot_return
    futures_pressure = (
        2.0
        * safe_div(
            joined["futures_taker_buy_quote"].astype(float),
            joined["futures_quote_volume"].astype(float),
        )
        - 1.0
    ).clip(-1.0, 1.0)
    spot_pressure = (
        2.0
        * safe_div(
            joined["spot_taker_buy_quote"].astype(float),
            joined["spot_quote_volume"].astype(float),
        )
        - 1.0
    ).clip(-1.0, 1.0)
    open_interest = joined["sum_open_interest"].astype(float).replace(0.0, np.nan)
    open_interest_change = np.log(open_interest).diff()
    window = int(rules["rolling_prior_bars"])
    minimum = int(rules["minimum_prior_bars"])
    futures_return_z, _, _ = common.rolling_z(
        futures_return,
        futures_return.shift(1),
        window,
        minimum,
    )
    spot_return_z, _, _ = common.rolling_z(
        spot_return,
        spot_return.shift(1),
        window,
        minimum,
    )
    relative_return_z, _, _ = common.rolling_z(
        relative_return,
        relative_return.shift(1),
        window,
        minimum,
    )
    futures_volume_z, _, _ = common.rolling_z(
        np.log1p(joined["futures_quote_volume"].astype(float)),
        np.log1p(joined["futures_quote_volume"].astype(float)).shift(1),
        window,
        minimum,
    )
    spot_volume_z, _, _ = common.rolling_z(
        np.log1p(joined["spot_quote_volume"].astype(float)),
        np.log1p(joined["spot_quote_volume"].astype(float)).shift(1),
        window,
        minimum,
    )
    open_interest_z, open_interest_mean, open_interest_standard = common.rolling_z(
        open_interest_change,
        open_interest_change.shift(1),
        window,
        minimum,
    )

    spot_direction = np.sign(spot_return)
    futures_direction = np.sign(futures_return)
    spot_directional_gap_z = -spot_direction * relative_return_z
    futures_directional_gap_z = futures_direction * relative_return_z
    spot_state = (
        (spot_return_z.abs() >= float(rules["absolute_leader_return_z_min"]))
        & (spot_volume_z >= float(rules["leader_quote_volume_z_min"]))
        & (
            spot_direction * spot_pressure
            >= float(rules["leader_directional_taker_pressure_min"])
        )
        & (
            spot_directional_gap_z
            >= float(rules["directional_lead_gap_z_min"])
        )
        & (
            open_interest_z
            >= float(rules["spot_led_open_interest_build_z_min"])
        )
    )
    perp_state = (
        (futures_return_z.abs() >= float(rules["absolute_leader_return_z_min"]))
        & (futures_volume_z >= float(rules["leader_quote_volume_z_min"]))
        & (
            futures_direction * futures_pressure
            >= float(rules["leader_directional_taker_pressure_min"])
        )
        & (
            futures_directional_gap_z
            >= float(rules["directional_lead_gap_z_min"])
        )
        & (
            open_interest_z
            <= float(rules["perp_led_open_interest_drop_z_max"])
        )
    )
    candidate = spot_state | perp_state

    futures_confirmation_return = futures_close.shift(-1) / futures_close - 1.0
    spot_confirmation_return = spot_close.shift(-1) / spot_close - 1.0
    index_confirmation_return = index_close.shift(-1) / index_close - 1.0
    confirmation_open_interest_z = (
        open_interest_change.shift(-1) - open_interest_mean
    ) / open_interest_standard
    confirmation_futures_pressure = futures_pressure.shift(-1)

    spot_event_gap = (
        spot_direction * (spot_return - futures_return)
    ).replace(0.0, np.nan)
    perp_event_gap = (
        futures_direction * (futures_return - spot_return)
    ).replace(0.0, np.nan)
    spot_confirmation_gap = spot_direction * (
        spot_confirmation_return - futures_confirmation_return
    )
    perp_confirmation_gap = futures_direction * (
        futures_confirmation_return - spot_confirmation_return
    )
    spot_gap_repair = 1.0 - spot_confirmation_gap / spot_event_gap
    perp_gap_repair = 1.0 - perp_confirmation_gap / perp_event_gap
    spot_index_follow_ratio = (
        spot_direction * index_confirmation_return / spot_return.abs().replace(0.0, np.nan)
    )
    perp_spot_follow_ratio = (
        futures_direction
        * spot_confirmation_return
        / futures_return.abs().replace(0.0, np.nan)
    )

    output = pd.DataFrame(index=joined.index)
    output["symbol"] = symbol
    output["spot_state"] = spot_state
    output["perp_state"] = perp_state
    output["spot_direction"] = spot_direction
    output["futures_direction"] = futures_direction
    output["spot_return_z"] = spot_return_z
    output["futures_return_z"] = futures_return_z
    output["spot_volume_z"] = spot_volume_z
    output["futures_volume_z"] = futures_volume_z
    output["relative_return_z"] = relative_return_z
    output["open_interest_z"] = open_interest_z
    output["spot_directional_taker_pressure"] = spot_direction * spot_pressure
    output["futures_directional_taker_pressure"] = (
        futures_direction * futures_pressure
    )
    output["spot_confirmation_follow"] = (
        spot_direction * spot_confirmation_return
    )
    output["futures_confirmation_follow_spot_direction"] = (
        spot_direction * futures_confirmation_return
    )
    output["futures_confirmation_follow_futures_direction"] = (
        futures_direction * futures_confirmation_return
    )
    output["confirmation_open_interest_z"] = confirmation_open_interest_z
    output["confirmation_futures_pressure_spot_direction"] = (
        spot_direction * confirmation_futures_pressure
    )
    output["confirmation_futures_pressure_futures_direction"] = (
        futures_direction * confirmation_futures_pressure
    )
    output["spot_gap_repair"] = spot_gap_repair
    output["perp_gap_repair"] = perp_gap_repair
    output["spot_index_follow_ratio"] = spot_index_follow_ratio
    output["perp_spot_follow_ratio"] = perp_spot_follow_ratio
    output["entry_ts"] = output.index + pd.Timedelta(minutes=5)
    output["entry_price"] = futures_close.shift(-1)
    output["spot_continuation_return"] = spot_direction * (
        futures_close.shift(-25) / futures_close.shift(-1) - 1.0
    )
    output["perp_reversal_return"] = -futures_direction * (
        futures_close.shift(-13) / futures_close.shift(-1) - 1.0
    )
    spot_event_score = (
        spot_return_z.abs()
        * np.maximum(spot_volume_z, 0.0)
        * np.maximum(open_interest_z, 0.0)
        * np.maximum(spot_direction * spot_pressure, 0.0)
        * np.maximum(spot_directional_gap_z, 0.0)
    )
    perp_event_score = (
        futures_return_z.abs()
        * np.maximum(futures_volume_z, 0.0)
        * np.maximum(-open_interest_z, 0.0)
        * np.maximum(futures_direction * futures_pressure, 0.0)
        * np.maximum(futures_directional_gap_z, 0.0)
    )
    output["event_score"] = np.where(spot_state, spot_event_score, perp_event_score)
    output = output[candidate].replace([np.inf, -np.inf], np.nan)
    required = [
        "spot_return_z",
        "futures_return_z",
        "spot_volume_z",
        "futures_volume_z",
        "relative_return_z",
        "open_interest_z",
        "confirmation_open_interest_z",
        "spot_gap_repair",
        "perp_gap_repair",
        "spot_index_follow_ratio",
        "perp_spot_follow_ratio",
        "spot_continuation_return",
        "perp_reversal_return",
        "event_score",
    ]
    return output.dropna(subset=required)


def add_spot_breadth(
    events: pd.DataFrame,
    spot_frames: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    if events.empty:
        return events
    returns = pd.DataFrame(
        {
            symbol: frame["close"].astype(float).pct_change(2)
            for symbol, frame in spot_frames.items()
        }
    )
    breadth: list[float] = []
    for _, row in events.iterrows():
        stamp = pd.Timestamp(row["entry_ts"])
        if stamp not in returns.index:
            breadth.append(float("nan"))
            continue
        values = returns.loc[stamp].dropna().to_numpy(dtype=float)
        direction = (
            float(row["spot_direction"])
            if bool(row["spot_state"])
            else float(row["futures_direction"])
        )
        breadth.append(
            float(np.mean(np.sign(values) == direction))
            if len(values)
            else float("nan")
        )
    output = events.copy()
    output["spot_cross_market_breadth"] = breadth
    return output.dropna(subset=["spot_cross_market_breadth"])


def classify_and_cooldown(
    events: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    spot_route = (
        events["spot_state"].astype(bool)
        & (events["spot_confirmation_follow"] >= 0.0)
        & (events["futures_confirmation_follow_spot_direction"] > 0.0)
        & (
            events["spot_gap_repair"]
            >= float(rules["spot_led_gap_repair_fraction_min"])
        )
        & (
            events["confirmation_open_interest_z"]
            >= float(rules["confirmation_open_interest_z_min"])
        )
        & (events["confirmation_futures_pressure_spot_direction"] >= 0.0)
        & (
            events["spot_index_follow_ratio"]
            >= float(rules["spot_led_index_follow_ratio_min"])
        )
        & (
            events["spot_cross_market_breadth"]
            >= float(rules["spot_led_cross_market_breadth_min"])
        )
    )
    perp_route = (
        events["perp_state"].astype(bool)
        & (events["futures_confirmation_follow_futures_direction"] <= 0.0)
        & (
            events["perp_gap_repair"]
            >= float(rules["perp_led_gap_repair_fraction_min"])
        )
        & (
            events["confirmation_open_interest_z"]
            >= float(rules["confirmation_open_interest_z_min"])
        )
        & (events["confirmation_futures_pressure_futures_direction"] <= 0.0)
        & (
            events["perp_spot_follow_ratio"]
            <= float(rules["perp_led_spot_follow_ratio_max"])
        )
    )
    output = events[spot_route | perp_route].copy()
    output["route"] = np.where(
        spot_route[spot_route | perp_route],
        SPOT_CONTINUATION,
        PERP_REVERSAL,
    )
    output["horizon_minutes"] = np.where(
        output["route"] == SPOT_CONTINUATION,
        int(rules["continuation_horizon_minutes"]),
        int(rules["reversal_horizon_minutes"]),
    )
    output["gross_return"] = np.where(
        output["route"] == SPOT_CONTINUATION,
        output["spot_continuation_return"],
        output["perp_reversal_return"],
    )
    output["net_return"] = (
        output["gross_return"]
        - float(rules["round_trip_cost_bps"]) / 10_000.0
    )
    spot_quality = (
        output["spot_gap_repair"].clip(lower=0.0)
        * output["spot_index_follow_ratio"].clip(lower=0.0)
        * output["spot_cross_market_breadth"]
        * output["open_interest_z"].clip(lower=0.0)
    )
    perp_quality = (
        output["perp_gap_repair"].clip(lower=0.0)
        * (
            float(rules["perp_led_spot_follow_ratio_max"])
            - output["perp_spot_follow_ratio"]
        ).clip(lower=0.0)
        * (-output["open_interest_z"]).clip(lower=0.0)
    )
    output["state_quality"] = np.where(
        output["route"] == SPOT_CONTINUATION,
        spot_quality,
        perp_quality,
    )
    output["rank_score"] = output["event_score"] * output["state_quality"]

    cooldown = pd.Timedelta(
        minutes=int(rules["same_symbol_event_cooldown_minutes"]),
    )
    accepted: list[pd.Series] = []
    for _, symbol_events in output.groupby("symbol"):
        next_allowed = pd.Timestamp.min.tz_localize("UTC")
        for event_ts, row in symbol_events.sort_index().iterrows():
            if event_ts < next_allowed:
                continue
            accepted.append(row)
            next_allowed = event_ts + cooldown
    if not accepted:
        return output.iloc[0:0]
    return pd.DataFrame(accepted).sort_values("entry_ts", kind="stable")


def arbitrate(events: pd.DataFrame) -> tuple[pd.DataFrame, Counter[str]]:
    if events.empty:
        return events, Counter()
    ordered = events.sort_values(
        ["entry_ts", "rank_score", "symbol"],
        ascending=[True, False, True],
        kind="stable",
    )
    chosen: list[pd.Series] = []
    skips: Counter[str] = Counter()
    free_at = pd.Timestamp.min.tz_localize("UTC")
    for timestamp, group in ordered.groupby("entry_ts", sort=True):
        winner = group.iloc[0]
        skips["SAME_EVENT_LOSER"] += max(0, len(group.index) - 1)
        if timestamp < free_at:
            skips["GLOBAL_POSITION_OCCUPIED"] += 1
            continue
        chosen.append(winner)
        free_at = timestamp + pd.Timedelta(
            minutes=int(winner["horizon_minutes"]),
        )
    if not chosen:
        return ordered.iloc[0:0], skips
    return pd.DataFrame(chosen).reset_index(drop=True), skips


def t_stat(values: pd.Series) -> float | None:
    if len(values.index) < 2:
        return None
    standard = float(values.std(ddof=1))
    if not math.isfinite(standard) or standard <= 0.0:
        return None
    return float(values.mean() / (standard / math.sqrt(len(values.index))))


def summarize(frame: pd.DataFrame, start: str, end: str) -> dict[str, Any]:
    lower, upper = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    sample = frame[
        (frame["entry_ts"] >= lower) & (frame["entry_ts"] < upper)
    ].copy()
    calendar_days = int((upper - lower).total_seconds() // 86_400)
    if sample.empty:
        return {
            "start": start,
            "end_exclusive": end,
            "calendar_days": calendar_days,
            "trades": 0,
            "trades_per_day": 0.0,
            "mean_gross_bps": None,
            "mean_net_bps": None,
            "net_t_stat": None,
            "win_rate": None,
            "payoff_ratio": None,
            "positive_month_share": 0.0,
            "positive_months": 0,
            "active_months": 0,
            "route_stats": {},
            "symbol_counts": {},
        }
    sample["entry_ts"] = pd.to_datetime(sample["entry_ts"], utc=True)
    monthly = (
        sample.set_index("entry_ts")["net_return"].resample("MS").mean().dropna()
    )
    wins = sample[sample["net_return"] > 0.0]["net_return"]
    losses = sample[sample["net_return"] < 0.0]["net_return"]
    payoff = None
    if len(wins.index) and len(losses.index):
        payoff = float(wins.mean() / abs(losses.mean()))
    route_stats: dict[str, Any] = {}
    for route, routed in sample.groupby("route"):
        route_stats[str(route)] = {
            "trades": len(routed.index),
            "mean_net_bps": float(routed["net_return"].mean() * 10_000.0),
            "win_rate": float((routed["net_return"] > 0.0).mean()),
            "net_t_stat": t_stat(routed["net_return"]),
        }
    return {
        "start": start,
        "end_exclusive": end,
        "calendar_days": calendar_days,
        "trades": len(sample.index),
        "trades_per_day": len(sample.index) / max(calendar_days, 1),
        "mean_gross_bps": float(sample["gross_return"].mean() * 10_000.0),
        "mean_net_bps": float(sample["net_return"].mean() * 10_000.0),
        "net_t_stat": t_stat(sample["net_return"]),
        "win_rate": float((sample["net_return"] > 0.0).mean()),
        "payoff_ratio": payoff,
        "positive_month_share": float((monthly > 0.0).mean()),
        "positive_months": int((monthly > 0.0).sum()),
        "active_months": len(monthly.index),
        "route_stats": route_stats,
        "symbol_counts": dict(Counter(sample["symbol"].astype(str))),
    }


def cross_split_routes(
    development: dict[str, Any],
    stability: dict[str, Any],
    holdout: dict[str, Any],
) -> dict[str, Any]:
    routes = set(development["route_stats"]) | set(stability["route_stats"]) | set(
        holdout["route_stats"]
    )
    output: dict[str, Any] = {}
    for route in sorted(routes):
        split_values: dict[str, Any] = {}
        positive_all = True
        for name, summary in (
            ("development", development),
            ("stability", stability),
            ("latest_holdout", holdout),
        ):
            stats = summary["route_stats"].get(route)
            split_values[name] = stats
            if stats is None or stats["trades"] == 0 or stats["mean_net_bps"] <= 0.0:
                positive_all = False
        output[route] = {
            "positive_across_all_declared_splits": positive_all,
            "splits": split_values,
        }
    return output


def render(payload: dict[str, Any]) -> str:
    lines = [
        "# Candidate 15 V18 — Spot/perpetual leadership state diagnostic",
        "",
        f"**{payload['classification']}**",
    ]
    for title, key in (
        ("Development", "development"),
        ("Year-long stability", "stability"),
        ("Latest July 2026 holdout", "latest_holdout"),
    ):
        summary = payload[key]
        lines.extend(
            (
                "",
                f"## {title}",
                f"- interval: `{summary['start']} -> {summary['end_exclusive']}`",
                f"- selected trades / day: `{summary['trades']} / {summary['trades_per_day']}`",
                f"- gross / net mean: `{summary['mean_gross_bps']} / {summary['mean_net_bps']}` bp",
                f"- win rate / payoff: `{summary['win_rate']} / {summary['payoff_ratio']}`",
                f"- net t-stat: `{summary['net_t_stat']}`",
                f"- positive months: `{summary['positive_months']} / {summary['active_months']}`",
                f"- route stats: `{summary['route_stats']}`",
                f"- symbol counts: `{summary['symbol_counts']}`",
            )
        )
    lines.extend(("", "## Advance checks"))
    lines.extend(
        f"- {name}: `{value}`" for name, value in payload["advance_checks"].items()
    )
    lines.extend(
        (
            "",
            "## Cross-split route evidence",
            f"`{payload['cross_split_routes']}`",
            "",
            "## Decision",
            payload["decision"],
            "",
            "This remains a mechanism screen. It neither synthesizes NAV nor replaces the required frozen NautilusTrader continuous-account validation.",
        )
    )
    return "\n".join(lines) + "\n"


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = common.load_json(protocol_path)
    data = protocol["data"]
    rules = protocol["fixed_state_rules"]
    evaluation = protocol["evaluation"]
    start = date.fromisoformat(data["start"])
    end = date.fromisoformat(data["end_exclusive"])
    output.mkdir(parents=True, exist_ok=True)

    futures_tasks: list[tuple[str, str, str, str, Path]] = []
    spot_tasks: list[tuple[str, str, str, Path]] = []
    metric_tasks: list[tuple[str, str, Path]] = []
    for symbol in data["symbols"]:
        for month in common.months(start, end):
            token = f"{month.year:04d}-{month.month:02d}"
            for dataset in ("klines", "indexPriceKlines"):
                destination = (
                    output
                    / "data"
                    / dataset
                    / symbol
                    / f"{symbol}-{data['interval']}-{token}.zip"
                )
                futures_tasks.append(
                    (dataset, symbol, data["interval"], token, destination),
                )
            spot_tasks.append(
                (
                    symbol,
                    data["interval"],
                    token,
                    output
                    / "data"
                    / "spot_klines"
                    / symbol
                    / f"{symbol}-{data['interval']}-{token}.zip",
                )
            )
        for day in oi_common.days(start, end):
            token = day.isoformat()
            metric_tasks.append(
                (
                    symbol,
                    token,
                    output
                    / "data"
                    / "metrics"
                    / symbol
                    / f"{symbol}-metrics-{token}.zip",
                )
            )

    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=24) as pool:
        jobs = [pool.submit(common.download_one, task) for task in futures_tasks]
        jobs.extend(pool.submit(download_spot, task) for task in spot_tasks)
        jobs.extend(pool.submit(oi_common.download_metric, task) for task in metric_tasks)
        for future in as_completed(jobs):
            records.append(future.result())
    common.write_json(output / "data_manifest.json", oi_common.aggregate_manifest(records))

    spot_frames: dict[str, pd.DataFrame] = {}
    futures_frames: dict[str, pd.DataFrame] = {}
    index_frames: dict[str, pd.DataFrame] = {}
    metrics_frames: dict[str, pd.DataFrame] = {}
    for symbol in data["symbols"]:
        futures_frames[symbol] = load_market_series(
            sorted((output / "data" / "klines" / symbol).glob("*.zip")),
            start,
            end,
        )
        spot_frames[symbol] = load_market_series(
            sorted((output / "data" / "spot_klines" / symbol).glob("*.zip")),
            start,
            end,
        )
        index_frames[symbol] = common.load_series(
            sorted(
                (output / "data" / "indexPriceKlines" / symbol).glob("*.zip")
            ),
            start,
            end,
            False,
        )
        metrics_frames[symbol] = metrics_adapter.load_metrics(
            sorted((output / "data" / "metrics" / symbol).glob("*.zip")),
            start,
            end,
        )

    event_parts = [
        symbol_state_events(
            symbol,
            spot_frames[symbol],
            futures_frames[symbol],
            index_frames[symbol],
            metrics_frames[symbol],
            rules,
        )
        for symbol in data["symbols"]
    ]
    raw_events = (
        pd.concat(event_parts, ignore_index=False).sort_index()
        if event_parts
        else pd.DataFrame()
    )
    with_breadth = add_spot_breadth(raw_events, spot_frames)
    routed = classify_and_cooldown(with_breadth, rules)
    routed.reset_index(names="event_ts").to_csv(
        output / "routed_events.csv",
        index=False,
    )
    selected, skips = arbitrate(routed)
    selected.to_csv(output / "selected_episodes.csv", index=False)

    development = summarize(
        selected,
        evaluation["development_start"],
        evaluation["development_end_exclusive"],
    )
    stability = summarize(
        selected,
        evaluation["stability_start"],
        evaluation["stability_end_exclusive"],
    )
    latest = summarize(
        selected,
        evaluation["latest_holdout_start"],
        evaluation["latest_holdout_end_exclusive"],
    )
    routes = cross_split_routes(development, stability, latest)
    gate = protocol["advance_gate"]
    concentration = (
        max(stability["symbol_counts"].values()) / stability["trades"]
        if stability["trades"]
        else 1.0
    )
    checks = {
        "positive_development_mean_net": (
            development["mean_net_bps"] is not None
            and development["mean_net_bps"] > 0.0
        ),
        "positive_stability_mean_net": (
            stability["mean_net_bps"] is not None
            and stability["mean_net_bps"]
            > float(gate["minimum_stability_mean_net_bps"])
        ),
        "stability_net_t_stat": (
            stability["net_t_stat"] is not None
            and stability["net_t_stat"]
            >= float(gate["minimum_stability_net_t_stat"])
        ),
        "stability_positive_month_share": (
            stability["positive_month_share"]
            >= float(gate["minimum_stability_positive_month_share"])
        ),
        "stability_independent_frequency": (
            stability["trades_per_day"]
            >= float(gate["minimum_stability_trades_per_calendar_day"])
        ),
        "positive_latest_holdout_mean_net": (
            latest["mean_net_bps"] is not None
            and latest["mean_net_bps"]
            > float(gate["minimum_latest_holdout_mean_net_bps"])
        ),
        "latest_holdout_trade_count": (
            latest["trades"] >= int(gate["minimum_latest_holdout_trades"])
        ),
        "symbol_concentration": (
            concentration <= float(gate["maximum_single_symbol_share"])
        ),
    }
    passed = all(checks.values())
    if passed:
        classification = "V18_MECHANISM_ADVANCES_TO_FROZEN_NAUTILUS"
        decision = (
            "The fixed router survived development, year-long stability and the latest "
            "July 2026 holdout. Freeze the route logic and implement same-leg stops and "
            "objectives in the existing NautilusTrader global portfolio runner."
        )
    else:
        classification = "V18_SPOT_PERP_ROUTER_REJECTED_OR_UNDERPOWERED"
        survivors = [
            route
            for route, evidence in routes.items()
            if evidence["positive_across_all_declared_splits"]
        ]
        decision = (
            "The integrated spot/perpetual router failed at least one predeclared gate. "
            f"Cross-split-positive routes: {survivors}. Do not tune numeric thresholds; "
            "retain only those routes and move to another independent mechanism."
        )
    payload = {
        "schema": "candidate-15-v18-summary-v1",
        "classification": classification,
        "advance_to_nautilus": passed,
        "raw_state_events": len(raw_events.index),
        "routed_events": len(routed.index),
        "selected_events": len(selected.index),
        "arbitration_skips": dict(skips),
        "development": development,
        "stability": stability,
        "latest_holdout": latest,
        "cross_split_routes": routes,
        "advance_checks": checks,
        "decision": decision,
    }
    common.write_json(output / "summary.json", payload)
    (output / "RESULT.md").write_text(render(payload), encoding="utf-8")
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
