#!/usr/bin/env python3
"""Candidate 15 V19 funding-settlement state-transition diagnostic.

The realized funding rate is deliberately ignored. Historical funding archives
supply only the settlement calendar, which is public in live trading through
``nextFundingTime`` / ``FundingRateUpdate``. Context is fixed from the last bar
completed at least five minutes before settlement. Entry waits for the first
bar completed after settlement. This is an economic mechanism screen and does
not synthesize portfolio NAV or replace NautilusTrader.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import re
import time
from typing import Any
from urllib.request import Request, urlopen
from zipfile import ZipFile

import numpy as np
import pandas as pd

import diagnose_v16_index_basis as common
import diagnose_v17_open_interest as oi_common
import diagnose_v18_spot_perp as spot_common
import run_v17_open_interest as metrics_adapter
import run_v18_spot_perp as market_adapter

UNWIND = "FUNDING_CROWD_UNWIND_REVERSAL"
CONTINUATION = "SPOT_CONFIRMED_FUNDING_CONTINUATION"


def funding_url(symbol: str, token: str) -> str:
    return (
        "https://data.binance.vision/data/futures/um/monthly/fundingRate/"
        f"{symbol}/{symbol}-fundingRate-{token}.zip"
    )


def download_funding(task: tuple[str, str, Path]) -> dict[str, Any]:
    symbol, token, destination = task
    url = funding_url(symbol, token)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.stat().st_size < 100:
        request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-15-v19"})
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
                        raise RuntimeError("corrupt funding archive")
                temporary.replace(destination)
                break
            except Exception as exc:
                last = exc
                if attempt == 4:
                    raise RuntimeError(f"download failed {url}: {last}") from exc
                time.sleep(2**attempt)
    payload = destination.read_bytes()
    return {
        "dataset": "fundingRate",
        "symbol": symbol,
        "month": token,
        "url": url,
        "path": str(destination),
        "bytes": len(payload),
        "sha256": sha256(payload).hexdigest(),
    }


def _normalize_column(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).replace("\ufeff", "").lower())


def read_funding_timestamp_column(path: Path) -> pd.Series:
    with ZipFile(path) as archive:
        members = [name for name in archive.namelist() if not name.endswith("/")]
        if len(members) != 1:
            raise RuntimeError(f"unexpected funding members in {path}: {members}")
        payload = archive.read(members[0])

    frame = pd.read_csv(BytesIO(payload), dtype=str)
    timestamp_column: Any | None = None
    preferred = {"fundingtime", "calctime", "timestamp", "time"}
    for column in frame.columns:
        if _normalize_column(column) in preferred:
            timestamp_column = column
            break
    if timestamp_column is None:
        for column in frame.columns:
            normalized = _normalize_column(column)
            if normalized.endswith("time") and "interval" not in normalized:
                timestamp_column = column
                break
    if timestamp_column is not None:
        return frame[timestamp_column].astype(str)

    # Headerless archives are read positionally. The first field is the
    # settlement calculation/funding timestamp in Binance Vision dumps.
    positional = pd.read_csv(BytesIO(payload), header=None, dtype=str)
    if positional.empty or positional.shape[1] < 1:
        raise RuntimeError(f"empty funding archive {path}")
    first = positional.iloc[:, 0].astype(str)
    normalized_first = first.str.replace("\ufeff", "", regex=False).str.strip()
    return normalized_first[~normalized_first.str.lower().isin(preferred)]


def parse_mixed_timestamps(values: pd.Series, *, source: Path) -> pd.DatetimeIndex:
    text = (
        values.astype(str)
        .str.replace("\ufeff", "", regex=False)
        .str.strip()
    )
    numeric = pd.to_numeric(text, errors="coerce")
    if numeric.notna().mean() >= 0.90:
        integer = numeric.round().astype("Int64")
        parsed = pd.Series(pd.NaT, index=integer.index, dtype="datetime64[ns, UTC]")
        masks = {
            "s": integer.between(1_000_000_000, 9_999_999_999),
            "ms": integer.between(1_000_000_000_000, 9_999_999_999_999),
            "us": integer.between(
                1_000_000_000_000_000,
                9_999_999_999_999_999,
            ),
            "ns": integer.between(
                1_000_000_000_000_000_000,
                9_999_999_999_999_999_999,
            ),
        }
        for unit, mask in masks.items():
            if bool(mask.any()):
                parsed.loc[mask] = pd.to_datetime(
                    integer.loc[mask].astype("int64"),
                    unit=unit,
                    utc=True,
                    errors="coerce",
                )
    else:
        parsed = pd.Series(
            pd.to_datetime(text, utc=True, errors="coerce"),
            index=text.index,
        )
    parsed = parsed.dropna()
    if parsed.empty:
        raise RuntimeError(f"no funding timestamps decoded from {source}")
    return pd.DatetimeIndex(parsed)


def load_funding_calendar(
    paths: list[Path],
    start: date,
    end: date,
) -> pd.DataFrame:
    if not paths:
        raise RuntimeError("no funding archives")
    timestamps: list[pd.DatetimeIndex] = []
    for path in paths:
        timestamps.append(
            parse_mixed_timestamps(
                read_funding_timestamp_column(path),
                source=path,
            )
        )
    actual = pd.DatetimeIndex(np.concatenate([index.to_numpy() for index in timestamps]))
    actual = pd.DatetimeIndex(actual, tz="UTC").sort_values().drop_duplicates()
    lower, upper = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    actual = actual[(actual >= lower) & (actual < upper)]
    if actual.empty:
        raise RuntimeError("funding calendar is empty after date filtering")

    boundary = actual.round("5min")
    distance = (actual - boundary).to_series(index=actual).abs()
    if bool((distance > pd.Timedelta(minutes=2)).any()):
        bad = distance[distance > pd.Timedelta(minutes=2)].index[0]
        raise RuntimeError(f"funding timestamp too far from five-minute grid: {bad}")
    frame = pd.DataFrame(
        {
            "funding_actual_ts": actual,
            "funding_boundary": boundary,
        }
    )
    frame = frame.drop_duplicates("funding_boundary", keep="last")
    frame = frame.sort_values("funding_boundary", kind="stable").reset_index(drop=True)
    frame["next_funding_boundary"] = frame["funding_boundary"].shift(-1)

    calendar_days = max((end - start).days, 1)
    if len(frame.index) / calendar_days < 2.0:
        raise RuntimeError(
            f"insufficient funding calendar density: {len(frame.index)} events "
            f"over {calendar_days} days",
        )
    return frame


def safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0.0, np.nan)


def build_symbol_frame(
    futures: pd.DataFrame,
    spot: pd.DataFrame,
    index_price: pd.DataFrame,
    premium: pd.DataFrame,
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
        .join(premium.rename(columns={"close": "premium_close"}), how="inner")
        .join(metrics, how="inner")
    )
    window = int(rules["rolling_prior_bars"])
    minimum = int(rules["minimum_prior_bars"])
    premium_close = joined["premium_close"].astype(float)
    open_interest = joined["sum_open_interest"].astype(float).replace(0.0, np.nan)
    log_open_interest = np.log(open_interest)
    open_interest_1h_change = log_open_interest.diff(12)
    open_interest_5m_change = log_open_interest.diff()
    futures_return = joined["futures_close"].astype(float).pct_change()
    futures_pressure = (
        2.0
        * safe_div(
            joined["futures_taker_buy_quote"].astype(float),
            joined["futures_quote_volume"].astype(float),
        )
        - 1.0
    ).clip(-1.0, 1.0)

    premium_z, _, _ = common.rolling_z(
        premium_close,
        premium_close.shift(1),
        window,
        minimum,
    )
    open_interest_1h_z, _, _ = common.rolling_z(
        open_interest_1h_change,
        open_interest_1h_change.shift(1),
        window,
        minimum,
    )
    open_interest_5m_z, _, _ = common.rolling_z(
        open_interest_5m_change,
        open_interest_5m_change.shift(1),
        window,
        minimum,
    )
    futures_return_z, _, _ = common.rolling_z(
        futures_return,
        futures_return.shift(1),
        window,
        minimum,
    )

    output = joined.loc[:, [
        "futures_close",
        "spot_close",
        "index_close",
        "premium_close",
    ]].copy()
    output["futures_pressure"] = futures_pressure
    output["premium_z"] = premium_z
    output["open_interest_1h_change"] = open_interest_1h_change
    output["open_interest_1h_z"] = open_interest_1h_z
    output["open_interest_5m_z"] = open_interest_5m_z
    output["futures_return_z"] = futures_return_z
    return output.replace([np.inf, -np.inf], np.nan)


def symbol_funding_events(
    symbol: str,
    frame: pd.DataFrame,
    funding: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    minimum_to_next = pd.Timedelta(
        minutes=int(rules["minimum_minutes_before_next_funding_after_entry"]),
    )
    reversal_horizon = int(rules["reversal_horizon_minutes"])
    continuation_horizon = int(rules["continuation_horizon_minutes"])
    maximum_horizon = max(reversal_horizon, continuation_horizon)

    for funding_row in funding.itertuples(index=False):
        boundary = pd.Timestamp(funding_row.funding_boundary)
        actual = pd.Timestamp(funding_row.funding_actual_ts)
        next_boundary = pd.Timestamp(funding_row.next_funding_boundary)
        if pd.isna(next_boundary):
            continue
        pre_ts = boundary - pd.Timedelta(minutes=5)
        post_ts = boundary + pd.Timedelta(minutes=5)
        entry_ts = post_ts
        maximum_target_ts = entry_ts + pd.Timedelta(minutes=maximum_horizon)
        if next_boundary - entry_ts < minimum_to_next:
            continue
        required_timestamps = (
            pre_ts,
            post_ts,
            entry_ts + pd.Timedelta(minutes=reversal_horizon),
            entry_ts + pd.Timedelta(minutes=continuation_horizon),
        )
        if any(timestamp not in frame.index for timestamp in required_timestamps):
            continue
        pre = frame.loc[pre_ts]
        post = frame.loc[post_ts]
        if isinstance(pre, pd.DataFrame) or isinstance(post, pd.DataFrame):
            raise RuntimeError(f"duplicate five-minute rows for {symbol} at {boundary}")
        required_values = (
            pre["futures_close"],
            pre["spot_close"],
            pre["index_close"],
            pre["premium_close"],
            pre["futures_pressure"],
            pre["premium_z"],
            pre["open_interest_1h_change"],
            pre["open_interest_1h_z"],
            post["futures_close"],
            post["spot_close"],
            post["index_close"],
            post["premium_close"],
            post["futures_pressure"],
            post["open_interest_5m_z"],
            post["futures_return_z"],
        )
        if not all(math.isfinite(float(value)) for value in required_values):
            continue

        crowd_direction = float(np.sign(float(pre["premium_close"])))
        if crowd_direction == 0.0:
            continue
        directional_premium_z = crowd_direction * float(pre["premium_z"])
        absolute_premium_bps = abs(float(pre["premium_close"])) * 10_000.0
        directional_pre_flow = crowd_direction * float(pre["futures_pressure"])
        if directional_premium_z < float(rules["directional_premium_z_min"]):
            continue
        if absolute_premium_bps < float(rules["absolute_premium_bps_min"]):
            continue
        if float(pre["open_interest_1h_change"]) <= 0.0:
            continue
        if float(pre["open_interest_1h_z"]) < float(
            rules["open_interest_1h_build_z_min"]
        ):
            continue
        if directional_pre_flow < float(
            rules["directional_pre_futures_taker_pressure_min"]
        ):
            continue

        futures_entry = float(post["futures_close"])
        post_extension = crowd_direction * (
            futures_entry / float(pre["futures_close"]) - 1.0
        )
        post_spot_follow = crowd_direction * (
            float(post["spot_close"]) / float(pre["spot_close"]) - 1.0
        )
        post_index_follow = crowd_direction * (
            float(post["index_close"]) / float(pre["index_close"]) - 1.0
        )
        post_flow = crowd_direction * float(post["futures_pressure"])
        post_return_z = crowd_direction * float(post["futures_return_z"])
        premium_repair = 1.0 - (
            abs(float(post["premium_close"]))
            / max(abs(float(pre["premium_close"])), 1e-12)
        )
        reversal_target = float(
            frame.loc[
                entry_ts + pd.Timedelta(minutes=reversal_horizon),
                "futures_close",
            ]
        )
        continuation_target = float(
            frame.loc[
                entry_ts + pd.Timedelta(minutes=continuation_horizon),
                "futures_close",
            ]
        )
        reversal_return = -crowd_direction * (
            reversal_target / futures_entry - 1.0
        )
        continuation_return = crowd_direction * (
            continuation_target / futures_entry - 1.0
        )
        context_score = (
            directional_premium_z
            * max(float(pre["open_interest_1h_z"]), 0.0)
            * max(directional_pre_flow, 0.0)
        )
        rows.append(
            {
                "symbol": symbol,
                "funding_actual_ts": actual,
                "event_ts": boundary,
                "pre_ts": pre_ts,
                "entry_ts": entry_ts,
                "next_funding_boundary": next_boundary,
                "crowd_direction": crowd_direction,
                "directional_premium_z": directional_premium_z,
                "absolute_premium_bps": absolute_premium_bps,
                "open_interest_1h_z": float(pre["open_interest_1h_z"]),
                "directional_pre_futures_taker_pressure": directional_pre_flow,
                "post_open_interest_5m_z": float(post["open_interest_5m_z"]),
                "post_directional_price_extension": post_extension,
                "post_directional_futures_taker_pressure": post_flow,
                "post_directional_return_z": post_return_z,
                "post_directional_spot_follow": post_spot_follow,
                "post_directional_index_follow": post_index_follow,
                "premium_repair_fraction": premium_repair,
                "entry_price": futures_entry,
                "reversal_return": reversal_return,
                "continuation_return": continuation_return,
                "context_score": context_score,
                "maximum_target_ts": maximum_target_ts,
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("event_ts", kind="stable")


def add_peer_spot_breadth(
    events: pd.DataFrame,
    spot_frames: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    if events.empty:
        return events
    two_bar_returns = pd.DataFrame(
        {
            symbol: frame["close"].astype(float).pct_change(2)
            for symbol, frame in spot_frames.items()
        }
    )
    breadth: list[float] = []
    for row in events.itertuples(index=False):
        timestamp = pd.Timestamp(row.entry_ts)
        if timestamp not in two_bar_returns.index:
            breadth.append(float("nan"))
            continue
        values = two_bar_returns.loc[timestamp].dropna().to_numpy(dtype=float)
        breadth.append(
            float(np.mean(np.sign(values) == float(row.crowd_direction)))
            if len(values)
            else float("nan")
        )
    output = events.copy()
    output["peer_spot_breadth"] = breadth
    return output.dropna(subset=["peer_spot_breadth"])


def classify(events: pd.DataFrame, rules: dict[str, Any]) -> pd.DataFrame:
    if events.empty:
        return events
    unwind = (
        (
            events["post_open_interest_5m_z"]
            <= float(rules["unwind_post_open_interest_5m_z_max"])
        )
        & (
            events["post_directional_price_extension"]
            <= float(rules["unwind_post_directional_price_extension_max"])
        )
        & (
            events["post_directional_futures_taker_pressure"]
            <= float(
                rules["unwind_post_directional_futures_taker_pressure_max"]
            )
        )
        & (
            events["post_directional_spot_follow"]
            <= float(rules["unwind_post_directional_spot_follow_max"])
        )
        & (
            events["premium_repair_fraction"]
            >= float(rules["unwind_premium_repair_fraction_min"])
        )
    )
    continuation = (
        (
            events["post_open_interest_5m_z"]
            >= float(rules["continuation_post_open_interest_5m_z_min"])
        )
        & (
            events["post_directional_price_extension"]
            > float(rules["continuation_post_directional_price_extension_min"])
        )
        & (
            events["post_directional_futures_taker_pressure"]
            >= float(
                rules["continuation_post_directional_futures_taker_pressure_min"]
            )
        )
        & (
            events["post_directional_spot_follow"]
            > float(rules["continuation_post_directional_spot_follow_min"])
        )
        & (
            events["post_directional_index_follow"]
            > float(rules["continuation_post_directional_index_follow_min"])
        )
        & (
            events["peer_spot_breadth"]
            >= float(rules["continuation_peer_spot_breadth_min"])
        )
    )
    routed = events[unwind | continuation].copy()
    if routed.empty:
        return routed
    routed_unwind = unwind.loc[routed.index]
    routed["route"] = np.where(routed_unwind, UNWIND, CONTINUATION)
    routed["direction"] = np.where(
        routed["route"] == UNWIND,
        -routed["crowd_direction"],
        routed["crowd_direction"],
    )
    routed["horizon_minutes"] = np.where(
        routed["route"] == UNWIND,
        int(rules["reversal_horizon_minutes"]),
        int(rules["continuation_horizon_minutes"]),
    )
    routed["gross_return"] = np.where(
        routed["route"] == UNWIND,
        routed["reversal_return"],
        routed["continuation_return"],
    )
    routed["cost_return"] = float(rules["round_trip_cost_bps"]) / 10_000.0
    routed["net_return"] = routed["gross_return"] - routed["cost_return"]

    unwind_quality = (
        (-routed["post_open_interest_5m_z"]).clip(lower=0.0)
        * (-routed["post_directional_futures_taker_pressure"]).clip(lower=0.0)
        * routed["premium_repair_fraction"].clip(lower=0.0)
        * (-routed["post_directional_return_z"]).clip(lower=0.0)
    )
    continuation_quality = (
        (routed["post_open_interest_5m_z"] + 0.25).clip(lower=0.01)
        * routed["post_directional_return_z"].clip(lower=0.0)
        * routed["peer_spot_breadth"]
    )
    routed["state_quality"] = np.where(
        routed["route"] == UNWIND,
        unwind_quality,
        continuation_quality,
    )
    routed["rank_score"] = routed["context_score"] * routed["state_quality"]
    return routed.replace([np.inf, -np.inf], np.nan).dropna(
        subset=["rank_score", "gross_return", "net_return"],
    )


def arbitrate(events: pd.DataFrame) -> tuple[pd.DataFrame, Counter[str]]:
    if events.empty:
        return events, Counter()
    ordered = events.sort_values(
        ["event_ts", "rank_score", "symbol"],
        ascending=[True, False, True],
        kind="stable",
    )
    selected: list[pd.Series] = []
    skips: Counter[str] = Counter()
    free_at = pd.Timestamp.min.tz_localize("UTC")
    for _, episode in ordered.groupby("event_ts", sort=True):
        winner = episode.iloc[0]
        skips["SAME_FUNDING_EVENT_LOSER"] += max(0, len(episode.index) - 1)
        entry_ts = pd.Timestamp(winner["entry_ts"])
        if entry_ts < free_at:
            skips["GLOBAL_POSITION_OCCUPIED"] += 1
            continue
        selected.append(winner)
        free_at = entry_ts + pd.Timedelta(
            minutes=int(winner["horizon_minutes"]),
        )
    if not selected:
        return ordered.iloc[0:0], skips
    return pd.DataFrame(selected).reset_index(drop=True), skips


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
        (pd.to_datetime(frame["entry_ts"], utc=True) >= lower)
        & (pd.to_datetime(frame["entry_ts"], utc=True) < upper)
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
    payoff_ratio = None
    if len(wins.index) and len(losses.index):
        payoff_ratio = float(wins.mean() / abs(losses.mean()))
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
        "payoff_ratio": payoff_ratio,
        "positive_month_share": float((monthly > 0.0).mean()),
        "positive_months": int((monthly > 0.0).sum()),
        "active_months": len(monthly.index),
        "route_stats": route_stats,
        "symbol_counts": dict(Counter(sample["symbol"].astype(str))),
    }


def cross_split_routes(
    development: dict[str, Any],
    stability: dict[str, Any],
    latest: dict[str, Any],
) -> dict[str, Any]:
    routes = set(development["route_stats"]) | set(stability["route_stats"]) | set(
        latest["route_stats"]
    )
    result: dict[str, Any] = {}
    for route in sorted(routes):
        splits: dict[str, Any] = {}
        positive_all = True
        for split_name, summary in (
            ("development", development),
            ("stability", stability),
            ("latest_pulse", latest),
        ):
            stats = summary["route_stats"].get(route)
            splits[split_name] = stats
            if stats is None or stats["trades"] == 0 or stats["mean_net_bps"] <= 0.0:
                positive_all = False
        result[route] = {
            "positive_across_all_declared_splits": positive_all,
            "splits": splits,
        }
    return result


def render(payload: dict[str, Any]) -> str:
    lines = [
        "# Candidate 15 V19 — Funding-settlement state diagnostic",
        "",
        f"**{payload['classification']}**",
        "",
        "The realized funding-rate value is not used by the signal. Funding archives provide settlement timestamps only.",
    ]
    for title, key in (
        ("Development", "development"),
        ("Year-long stability", "stability"),
        ("Latest July 2026 pulse", "latest_pulse"),
    ):
        summary = payload[key]
        lines.extend(
            (
                "",
                f"## {title}",
                f"- interval: `{summary['start']} -> {summary['end_exclusive']}`",
                f"- selected independent episodes / day: `{summary['trades']} / {summary['trades_per_day']}`",
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
            "This mechanism screen does not synthesize account NAV. A surviving route still requires frozen NautilusTrader orders, event-extreme invalidation, current-NAV 3% risk sizing, one global slot and continuous-account validation.",
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
    funding_tasks: list[tuple[str, str, Path]] = []
    for symbol in data["symbols"]:
        for month in common.months(start, end):
            token = f"{month.year:04d}-{month.month:02d}"
            for dataset in ("klines", "indexPriceKlines", "premiumIndexKlines"):
                futures_tasks.append(
                    (
                        dataset,
                        symbol,
                        data["interval"],
                        token,
                        output
                        / "data"
                        / dataset
                        / symbol
                        / f"{symbol}-{data['interval']}-{token}.zip",
                    )
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
            funding_tasks.append(
                (
                    symbol,
                    token,
                    output
                    / "data"
                    / "fundingRate"
                    / symbol
                    / f"{symbol}-fundingRate-{token}.zip",
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
        jobs.extend(pool.submit(spot_common.download_spot, task) for task in spot_tasks)
        jobs.extend(pool.submit(oi_common.download_metric, task) for task in metric_tasks)
        jobs.extend(pool.submit(download_funding, task) for task in funding_tasks)
        for future in as_completed(jobs):
            records.append(future.result())
    common.write_json(output / "data_manifest.json", oi_common.aggregate_manifest(records))

    spot_frames: dict[str, pd.DataFrame] = {}
    symbol_frames: dict[str, pd.DataFrame] = {}
    funding_calendars: dict[str, pd.DataFrame] = {}
    for symbol in data["symbols"]:
        futures = market_adapter.load_market_series(
            sorted((output / "data" / "klines" / symbol).glob("*.zip")),
            start,
            end,
        )
        spot = market_adapter.load_market_series(
            sorted((output / "data" / "spot_klines" / symbol).glob("*.zip")),
            start,
            end,
        )
        index_price = common.load_series(
            sorted(
                (output / "data" / "indexPriceKlines" / symbol).glob("*.zip")
            ),
            start,
            end,
            False,
        )
        premium = common.load_series(
            sorted(
                (output / "data" / "premiumIndexKlines" / symbol).glob("*.zip")
            ),
            start,
            end,
            False,
        )
        metrics = metrics_adapter.load_metrics(
            sorted((output / "data" / "metrics" / symbol).glob("*.zip")),
            start,
            end,
        )
        funding_calendars[symbol] = load_funding_calendar(
            sorted((output / "data" / "fundingRate" / symbol).glob("*.zip")),
            start,
            end,
        )
        spot_frames[symbol] = spot
        symbol_frames[symbol] = build_symbol_frame(
            futures,
            spot,
            index_price,
            premium,
            metrics,
            rules,
        )

    event_parts = [
        symbol_funding_events(
            symbol,
            symbol_frames[symbol],
            funding_calendars[symbol],
            rules,
        )
        for symbol in data["symbols"]
    ]
    event_parts = [part for part in event_parts if not part.empty]
    raw_events = (
        pd.concat(event_parts, ignore_index=True).sort_values("event_ts")
        if event_parts
        else pd.DataFrame()
    )
    with_breadth = add_peer_spot_breadth(raw_events, spot_frames)
    routed = classify(with_breadth, rules)
    routed.to_csv(output / "routed_events.csv", index=False)
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
        evaluation["latest_pulse_start"],
        evaluation["latest_pulse_end_exclusive"],
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
        "positive_latest_pulse_mean_net": (
            latest["mean_net_bps"] is not None
            and latest["mean_net_bps"]
            > float(gate["minimum_latest_pulse_mean_net_bps"])
        ),
        "latest_pulse_trade_count": (
            latest["trades"] >= int(gate["minimum_latest_pulse_trades"])
        ),
        "symbol_concentration": (
            concentration <= float(gate["maximum_single_symbol_share"])
        ),
    }
    passed = all(checks.values())
    if passed:
        classification = "V19_MECHANISM_ADVANCES_TO_FROZEN_NAUTILUS"
        decision = (
            "The fixed funding-settlement router survived development, year-long "
            "stability and the latest pulse. Freeze it and implement event-extreme "
            "invalidation and post-settlement objectives in the existing NautilusTrader "
            "global portfolio runner without changing state logic."
        )
    else:
        classification = "V19_FUNDING_SETTLEMENT_ROUTER_REJECTED_OR_UNDERPOWERED"
        survivors = [
            route
            for route, evidence in routes.items()
            if evidence["positive_across_all_declared_splits"]
        ]
        decision = (
            "The integrated funding-settlement router failed at least one predeclared "
            f"gate. Cross-split-positive routes: {survivors}. Do not tune numeric "
            "thresholds; retain only those routes and move to another independent "
            "causal family."
        )
    payload = {
        "schema": "candidate-15-v19-summary-v1",
        "classification": classification,
        "advance_to_nautilus": passed,
        "realized_funding_rate_used": False,
        "raw_context_events": len(raw_events.index),
        "routed_events": len(routed.index),
        "selected_events": len(selected.index),
        "funding_event_counts": {
            symbol: len(calendar.index)
            for symbol, calendar in funding_calendars.items()
        },
        "arbitration_skips": dict(skips),
        "development": development,
        "stability": stability,
        "latest_pulse": latest,
        "cross_split_routes": routes,
        "advance_checks": checks,
        "latest_pulse_status": evaluation["latest_pulse_status"],
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
