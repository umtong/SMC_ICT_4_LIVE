#!/usr/bin/env python3
"""Candidate 15 V21 prior-only co-jump state-transition diagnostic.

Continuous five-minute volatility is estimated from rolling bipower variation
using returns completed before the event.  An event return is then separated
into systemic spot/index co-jump acceptance versus futures-only leverage jump
reclaim.  Route confirmation uses the next completed bar.  This is an economic
mechanism screen, not a portfolio/NAV or matching engine; surviving logic must
still be frozen inside NautilusTrader.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
import time
from typing import Any, Iterable
from urllib.request import Request, urlopen
from zipfile import ZipFile

import numpy as np
import pandas as pd

import diagnose_v16_index_basis as common
import diagnose_v17_open_interest as oi_common
import run_v17_open_interest as metrics_adapter
import run_v18_spot_perp as market_adapter

SYSTEMIC = "SYSTEMIC_COJUMP_ACCEPTANCE"
IDIOSYNCRATIC = "IDIOSYNCRATIC_LEVERAGE_JUMP_RECLAIM"


def safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0.0, np.nan)


def archive_url(
    market: str,
    cadence: str,
    dataset: str,
    symbol: str,
    interval: str,
    token: str,
) -> str:
    filename = f"{symbol}-{interval}-{token}.zip"
    if market == "spot":
        if dataset != "klines":
            raise ValueError((market, dataset))
        return (
            f"https://data.binance.vision/data/spot/{cadence}/klines/"
            f"{symbol}/{interval}/{filename}"
        )
    if market == "futures":
        return (
            f"https://data.binance.vision/data/futures/um/{cadence}/{dataset}/"
            f"{symbol}/{interval}/{filename}"
        )
    raise ValueError(market)


def download_archive(
    task: tuple[str, str, str, str, str, str, Path],
) -> dict[str, Any]:
    market, cadence, dataset, symbol, interval, token, destination = task
    url = archive_url(market, cadence, dataset, symbol, interval, token)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.stat().st_size < 100:
        request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-15-v21"})
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
                    bad = archive.testzip()
                    if bad is not None:
                        raise RuntimeError(f"corrupt member {bad}")
                temporary.replace(destination)
                break
            except Exception as exc:
                last = exc
                if attempt == 4:
                    raise RuntimeError(f"download failed {url}: {last}") from exc
                time.sleep(2**attempt)
    payload = destination.read_bytes()
    return {
        "dataset": f"{market}_{dataset}_{cadence}",
        "symbol": symbol,
        "token": token,
        "url": url,
        "path": str(destination),
        "bytes": len(payload),
        "sha256": sha256(payload).hexdigest(),
    }


def normalize_open_times(values: pd.Series) -> pd.DatetimeIndex:
    integer = pd.to_numeric(values, errors="raise").astype("int64")
    normalized_ms = integer.copy()
    microseconds = normalized_ms >= 1_000_000_000_000_000
    milliseconds = (
        (normalized_ms >= 1_000_000_000_000)
        & (normalized_ms < 10_000_000_000_000)
    )
    if not bool((microseconds | milliseconds).all()):
        bad = int(normalized_ms[~(microseconds | milliseconds)].iloc[0])
        raise RuntimeError(f"unsupported kline timestamp magnitude {bad}")
    normalized_ms.loc[microseconds] = normalized_ms.loc[microseconds] // 1_000
    return pd.to_datetime(normalized_ms, unit="ms", utc=True)


def load_index_series(
    paths: list[Path],
    start: date,
    end: date,
) -> pd.DataFrame:
    if not paths:
        raise RuntimeError("no index archives")
    raw = pd.concat([common.read_zip(path) for path in paths], ignore_index=True)
    raw = raw.drop_duplicates("open_time", keep="last").sort_values("open_time")
    index = normalize_open_times(raw["open_time"]) + pd.Timedelta(minutes=5)
    output = pd.DataFrame(index=index)
    output["close"] = pd.to_numeric(raw["close"], errors="coerce").to_numpy()
    output = output.dropna()
    output = output[~output.index.duplicated(keep="last")].sort_index()
    lower, upper = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    output = output[(output.index > lower) & (output.index <= upper)]
    expected = int((upper - lower).total_seconds() // 300)
    coverage = len(output.index) / max(expected, 1)
    if coverage < 0.995:
        raise RuntimeError(
            f"insufficient index coverage: {len(output.index)}/{expected} "
            f"({coverage:.6f})",
        )
    return output


def bipower_sigma(
    log_return: pd.Series,
    lookback: int,
    minimum: int,
) -> pd.Series:
    # Both factors precede the event return. pi/2 rescales E|Z1||Z2| to
    # variance for independent Gaussian continuous returns while reducing the
    # influence of isolated jumps relative to rolling squared returns.
    product = log_return.shift(1).abs() * log_return.shift(2).abs()
    variance = (
        (math.pi / 2.0)
        * product.rolling(lookback, min_periods=minimum).mean()
    )
    return np.sqrt(variance.clip(lower=1e-18))


def total_cost(rules: dict[str, Any]) -> float:
    return (
        float(rules["execution_round_trip_cost_bps"])
        + float(rules["funding_and_unmodeled_impact_reserve_bps"])
    ) / 10_000.0


def build_symbol_frame(
    futures: pd.DataFrame,
    spot: pd.DataFrame,
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
    futures_return = np.log(futures_close).diff()
    spot_return = np.log(spot_close).diff()
    index_return = np.log(index_close).diff()
    lookback = int(rules["bipower_lookback_bars"])
    minimum = int(rules["bipower_minimum_prior_bars"])
    futures_sigma = bipower_sigma(futures_return, lookback, minimum)
    spot_sigma = bipower_sigma(spot_return, lookback, minimum)
    index_sigma = bipower_sigma(index_return, lookback, minimum)
    futures_jump_z = safe_div(futures_return, futures_sigma)
    spot_jump_z = safe_div(spot_return, spot_sigma)
    index_jump_z = safe_div(index_return, index_sigma)

    futures_pressure = (
        2.0
        * safe_div(
            joined["futures_taker_buy_quote"].astype(float),
            joined["futures_quote_volume"].astype(float),
        )
        - 1.0
    ).clip(-1.0, 1.0)
    log_volume = np.log1p(joined["futures_quote_volume"].astype(float))
    state_window = int(rules["state_rolling_prior_bars"])
    state_minimum = int(rules["state_minimum_prior_bars"])
    volume_z, _, _ = common.rolling_z(
        log_volume,
        log_volume.shift(1),
        state_window,
        state_minimum,
    )
    log_open_interest = np.log(
        joined["sum_open_interest"].astype(float).replace(0.0, np.nan)
    )
    open_interest_change = log_open_interest.diff()
    open_interest_5m_z, _, _ = common.rolling_z(
        open_interest_change,
        open_interest_change.shift(1),
        state_window,
        state_minimum,
    )
    perp_spot_gap = futures_return - spot_return
    perp_spot_gap_z, _, _ = common.rolling_z(
        perp_spot_gap,
        perp_spot_gap.shift(1),
        state_window,
        state_minimum,
    )

    output = joined.loc[:, ["futures_close", "spot_close", "index_close"]].copy()
    output["futures_return"] = futures_return
    output["spot_return"] = spot_return
    output["index_return"] = index_return
    output["futures_jump_z"] = futures_jump_z
    output["spot_jump_z"] = spot_jump_z
    output["index_jump_z"] = index_jump_z
    output["futures_volume_z"] = volume_z
    output["futures_pressure"] = futures_pressure
    output["open_interest_5m_z"] = open_interest_5m_z
    output["perp_spot_gap_z"] = perp_spot_gap_z
    return output.replace([np.inf, -np.inf], np.nan)


def add_cross_market_features(
    frames: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    spot_jump = pd.DataFrame(
        {symbol: frame["spot_jump_z"] for symbol, frame in frames.items()}
    )
    spot_return = pd.DataFrame(
        {symbol: frame["spot_return"] for symbol, frame in frames.items()}
    )
    output: dict[str, pd.DataFrame] = {}
    for symbol, frame in frames.items():
        enriched = frame.copy()
        direction = np.sign(enriched["futures_return"])
        aligned_jump = spot_jump.mul(direction, axis=0)
        aligned_return = spot_return.mul(direction, axis=0)
        enriched["event_peer_spot_jump_breadth"] = (
            aligned_jump >= 3.0
        ).mean(axis=1)
        enriched["confirmation_peer_spot_breadth"] = (
            aligned_return.shift(-1) > 0.0
        ).mean(axis=1)
        output[symbol] = enriched
    return output


def candidate_events(
    symbol: str,
    frame: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    direction = np.sign(frame["futures_return"])
    base = (
        (frame["futures_jump_z"].abs() >= float(rules["absolute_jump_z_min"]))
        & (
            frame["futures_volume_z"]
            >= float(rules["futures_quote_volume_z_min"])
        )
        & (
            direction * frame["futures_pressure"]
            >= float(rules["directional_event_futures_taker_pressure_min"])
        )
    )
    systemic = (
        base
        & (
            direction * frame["spot_jump_z"]
            >= float(rules["systemic_spot_jump_z_min"])
        )
        & (
            direction * frame["index_jump_z"]
            >= float(rules["systemic_index_jump_z_min"])
        )
        & (
            frame["event_peer_spot_jump_breadth"]
            >= float(rules["systemic_peer_spot_jump_breadth_min"])
        )
        & (
            frame["open_interest_5m_z"]
            >= float(rules["systemic_event_open_interest_5m_z_min"])
        )
    )
    idiosyncratic = (
        base
        & (
            direction * frame["spot_jump_z"]
            <= float(rules["idiosyncratic_spot_jump_z_max"])
        )
        & (
            direction * frame["index_jump_z"]
            <= float(rules["idiosyncratic_index_jump_z_max"])
        )
        & (
            direction * frame["perp_spot_gap_z"]
            >= float(rules["idiosyncratic_directional_perp_spot_gap_z_min"])
        )
        & (
            frame["open_interest_5m_z"]
            <= float(rules["idiosyncratic_event_open_interest_5m_z_max"])
        )
    )
    candidates = frame[systemic | idiosyncratic].copy()
    if candidates.empty:
        return pd.DataFrame()
    candidates["symbol"] = symbol
    candidates["event_ts"] = candidates.index
    candidates["event_direction"] = direction.loc[candidates.index]
    candidates["systemic_state"] = systemic.loc[candidates.index]
    candidates["idiosyncratic_state"] = idiosyncratic.loc[candidates.index]

    confirmation = frame.shift(-1).loc[candidates.index]
    candidates["entry_ts"] = candidates.index + pd.Timedelta(minutes=5)
    candidates["entry_price"] = confirmation["futures_close"].to_numpy()
    candidates["confirmation_price_extension"] = (
        candidates["event_direction"].to_numpy()
        * (
            confirmation["futures_close"].to_numpy()
            / candidates["futures_close"].to_numpy()
            - 1.0
        )
    )
    candidates["confirmation_open_interest_5m_z"] = confirmation[
        "open_interest_5m_z"
    ].to_numpy()
    candidates["confirmation_directional_futures_taker_pressure"] = (
        candidates["event_direction"].to_numpy()
        * confirmation["futures_pressure"].to_numpy()
    )
    candidates["confirmation_directional_spot_follow"] = (
        candidates["event_direction"].to_numpy()
        * confirmation["spot_return"].to_numpy()
    )
    candidates["confirmation_directional_index_follow"] = (
        candidates["event_direction"].to_numpy()
        * confirmation["index_return"].to_numpy()
    )
    candidates["confirmation_directional_futures_jump_z"] = (
        candidates["event_direction"].to_numpy()
        * confirmation["futures_jump_z"].to_numpy()
    )
    candidates["confirmation_peer_spot_breadth"] = frame.loc[
        candidates.index,
        "confirmation_peer_spot_breadth",
    ].to_numpy()

    continuation_steps = 1 + int(rules["continuation_horizon_minutes"]) // 5
    reclaim_steps = 1 + int(rules["reclaim_horizon_minutes"]) // 5
    candidates["continuation_target_price"] = frame["futures_close"].shift(
        -continuation_steps
    ).loc[candidates.index].to_numpy()
    candidates["reclaim_target_price"] = frame["futures_close"].shift(
        -reclaim_steps
    ).loc[candidates.index].to_numpy()
    candidates["continuation_gross_return"] = (
        candidates["event_direction"]
        * (candidates["continuation_target_price"] / candidates["entry_price"] - 1.0)
    )
    candidates["reclaim_gross_return"] = (
        -candidates["event_direction"]
        * (candidates["reclaim_target_price"] / candidates["entry_price"] - 1.0)
    )
    required = [
        "entry_price",
        "confirmation_price_extension",
        "confirmation_open_interest_5m_z",
        "confirmation_directional_futures_taker_pressure",
        "confirmation_directional_spot_follow",
        "confirmation_directional_index_follow",
        "confirmation_directional_futures_jump_z",
        "confirmation_peer_spot_breadth",
        "continuation_gross_return",
        "reclaim_gross_return",
    ]
    return candidates.replace([np.inf, -np.inf], np.nan).dropna(subset=required)


def classify_and_cooldown(
    events: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    if events.empty:
        return events
    systemic_confirmation = (
        events["systemic_state"].astype(bool)
        & (
            events["confirmation_price_extension"]
            > float(rules["systemic_confirmation_price_extension_min"])
        )
        & (
            events["confirmation_open_interest_5m_z"]
            >= float(rules["systemic_confirmation_open_interest_5m_z_min"])
        )
        & (
            events["confirmation_directional_futures_taker_pressure"]
            >= float(
                rules[
                    "systemic_confirmation_directional_futures_taker_pressure_min"
                ]
            )
        )
        & (
            events["confirmation_directional_spot_follow"]
            > float(rules["systemic_confirmation_directional_spot_follow_min"])
        )
        & (
            events["confirmation_directional_index_follow"]
            > float(rules["systemic_confirmation_directional_index_follow_min"])
        )
        & (
            events["confirmation_peer_spot_breadth"]
            >= float(rules["systemic_confirmation_peer_spot_breadth_min"])
        )
    )
    idiosyncratic_confirmation = (
        events["idiosyncratic_state"].astype(bool)
        & (
            events["confirmation_price_extension"]
            <= float(rules["idiosyncratic_confirmation_price_extension_max"])
        )
        & (
            events["confirmation_open_interest_5m_z"]
            >= float(rules["idiosyncratic_confirmation_open_interest_5m_z_min"])
        )
        & (
            events["confirmation_directional_futures_taker_pressure"]
            <= float(
                rules[
                    "idiosyncratic_confirmation_directional_futures_taker_pressure_max"
                ]
            )
        )
        & (
            events["confirmation_directional_spot_follow"]
            <= float(rules["idiosyncratic_confirmation_directional_spot_follow_max"])
        )
    )
    routed = events[systemic_confirmation | idiosyncratic_confirmation].copy()
    if routed.empty:
        return routed
    is_systemic = systemic_confirmation.loc[routed.index]
    routed["route"] = np.where(is_systemic, SYSTEMIC, IDIOSYNCRATIC)
    routed["trade_direction"] = np.where(
        routed["route"] == SYSTEMIC,
        routed["event_direction"],
        -routed["event_direction"],
    )
    routed["horizon_minutes"] = np.where(
        routed["route"] == SYSTEMIC,
        int(rules["continuation_horizon_minutes"]),
        int(rules["reclaim_horizon_minutes"]),
    )
    routed["gross_return"] = np.where(
        routed["route"] == SYSTEMIC,
        routed["continuation_gross_return"],
        routed["reclaim_gross_return"],
    )
    routed["cost_return"] = total_cost(rules)
    routed["net_return"] = routed["gross_return"] - routed["cost_return"]

    systemic_context = (
        routed["futures_jump_z"].abs()
        * routed["futures_volume_z"].clip(lower=0.0)
        * routed["event_peer_spot_jump_breadth"]
        * (routed["open_interest_5m_z"] + 0.25).clip(lower=0.01)
    )
    idiosyncratic_context = (
        routed["futures_jump_z"].abs()
        * routed["futures_volume_z"].clip(lower=0.0)
        * (
            routed["event_direction"] * routed["perp_spot_gap_z"]
        ).clip(lower=0.0)
        * (-routed["open_interest_5m_z"]).clip(lower=0.0)
    )
    systemic_quality = (
        routed["confirmation_directional_futures_jump_z"].clip(lower=0.0)
        * (routed["confirmation_open_interest_5m_z"] + 0.25).clip(lower=0.01)
        * routed["confirmation_peer_spot_breadth"]
    )
    idiosyncratic_quality = (
        (-routed["confirmation_directional_futures_jump_z"]).clip(lower=0.0)
        * (
            -routed["confirmation_directional_futures_taker_pressure"]
        ).clip(lower=0.0)
        * (routed["confirmation_open_interest_5m_z"] + 0.25).clip(lower=0.01)
    )
    routed["context_score"] = np.where(
        routed["route"] == SYSTEMIC,
        systemic_context,
        idiosyncratic_context,
    )
    routed["state_quality"] = np.where(
        routed["route"] == SYSTEMIC,
        systemic_quality,
        idiosyncratic_quality,
    )
    routed["rank_score"] = routed["context_score"] * routed["state_quality"]
    routed = routed.replace([np.inf, -np.inf], np.nan).dropna(
        subset=["rank_score", "gross_return", "net_return"],
    )

    cooldown = pd.Timedelta(
        minutes=int(rules["same_symbol_event_cooldown_minutes"]),
    )
    accepted: list[pd.Series] = []
    for _, symbol_events in routed.groupby("symbol"):
        next_allowed = pd.Timestamp.min.tz_localize("UTC")
        for _, row in symbol_events.sort_values("event_ts").iterrows():
            event_ts = pd.Timestamp(row["event_ts"])
            if event_ts < next_allowed:
                continue
            accepted.append(row)
            next_allowed = event_ts + cooldown
    if not accepted:
        return routed.iloc[0:0]
    return pd.DataFrame(accepted).sort_values("event_ts", kind="stable")


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
        skips["SAME_COJUMP_EVENT_LOSER"] += max(0, len(episode.index) - 1)
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
    entry_ts = pd.to_datetime(frame["entry_ts"], utc=True)
    sample = frame[(entry_ts >= lower) & (entry_ts < upper)].copy()
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


def cross_split_routes(splits: dict[str, dict[str, Any]]) -> dict[str, Any]:
    routes: set[str] = set()
    for summary in splits.values():
        routes.update(summary["route_stats"])
    result: dict[str, Any] = {}
    for route in sorted(routes):
        evidence: dict[str, Any] = {}
        positive_all = True
        for split_name, summary in splits.items():
            stats = summary["route_stats"].get(route)
            evidence[split_name] = stats
            if stats is None or stats["trades"] == 0 or stats["mean_net_bps"] <= 0.0:
                positive_all = False
        result[route] = {
            "positive_across_all_declared_splits": positive_all,
            "splits": evidence,
        }
    return result


def render(payload: dict[str, Any]) -> str:
    lines = [
        "# Candidate 15 V21 — Prior-only co-jump state diagnostic",
        "",
        f"**{payload['classification']}**",
        "",
        "Jump statistics use prior-only bipower variation. The latest August pulse uses daily archives ending before 2026-08-08.",
    ]
    for title, key in (
        ("Development", "development"),
        ("Year-long stability", "stability"),
        ("July 2026 confirmation", "july_confirmation"),
        ("Latest August 1-7 pulse", "latest_august_pulse"),
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
            "A family-level pass is not final-system success. Any survivor still requires frozen NautilusTrader orders, same-leg invalidation, 3% current-NAV risk sizing, actual funding/fees, one global slot and continuous-account validation with at least one independent completed trade per calendar day.",
        )
    )
    return "\n".join(lines) + "\n"


def month_tokens(start: date, end_exclusive: date) -> Iterable[str]:
    for month in common.months(start, end_exclusive):
        yield f"{month.year:04d}-{month.month:02d}"


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = common.load_json(protocol_path)
    data = protocol["data"]
    rules = protocol["fixed_rules"]
    evaluation = protocol["evaluation"]
    start = date.fromisoformat(data["start"])
    monthly_end = date.fromisoformat(data["monthly_end_exclusive"])
    end = date.fromisoformat(data["end_exclusive"])
    output.mkdir(parents=True, exist_ok=True)

    archive_tasks: list[tuple[str, str, str, str, str, str, Path]] = []
    for symbol in data["symbols"]:
        for token in month_tokens(start, monthly_end):
            archive_tasks.extend(
                (
                    (
                        "futures",
                        "monthly",
                        "klines",
                        symbol,
                        data["interval"],
                        token,
                        output
                        / "data"
                        / "futures_monthly"
                        / "klines"
                        / symbol
                        / f"{symbol}-{data['interval']}-{token}.zip",
                    ),
                    (
                        "futures",
                        "monthly",
                        "indexPriceKlines",
                        symbol,
                        data["interval"],
                        token,
                        output
                        / "data"
                        / "futures_monthly"
                        / "indexPriceKlines"
                        / symbol
                        / f"{symbol}-{data['interval']}-{token}.zip",
                    ),
                    (
                        "spot",
                        "monthly",
                        "klines",
                        symbol,
                        data["interval"],
                        token,
                        output
                        / "data"
                        / "spot_monthly"
                        / "klines"
                        / symbol
                        / f"{symbol}-{data['interval']}-{token}.zip",
                    ),
                )
            )
        for token in data["latest_daily_dates"]:
            archive_tasks.extend(
                (
                    (
                        "futures",
                        "daily",
                        "klines",
                        symbol,
                        data["interval"],
                        token,
                        output
                        / "data"
                        / "futures_daily"
                        / "klines"
                        / symbol
                        / f"{symbol}-{data['interval']}-{token}.zip",
                    ),
                    (
                        "futures",
                        "daily",
                        "indexPriceKlines",
                        symbol,
                        data["interval"],
                        token,
                        output
                        / "data"
                        / "futures_daily"
                        / "indexPriceKlines"
                        / symbol
                        / f"{symbol}-{data['interval']}-{token}.zip",
                    ),
                    (
                        "spot",
                        "daily",
                        "klines",
                        symbol,
                        data["interval"],
                        token,
                        output
                        / "data"
                        / "spot_daily"
                        / "klines"
                        / symbol
                        / f"{symbol}-{data['interval']}-{token}.zip",
                    ),
                )
            )
    metric_tasks: list[tuple[str, str, Path]] = []
    for symbol in data["symbols"]:
        cursor = start
        while cursor < end:
            token = cursor.isoformat()
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
            cursor += timedelta(days=1)

    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=24) as pool:
        jobs = [pool.submit(download_archive, task) for task in archive_tasks]
        jobs.extend(pool.submit(oi_common.download_metric, task) for task in metric_tasks)
        for future in as_completed(jobs):
            records.append(future.result())
    common.write_json(output / "data_manifest.json", oi_common.aggregate_manifest(records))

    frames: dict[str, pd.DataFrame] = {}
    for symbol in data["symbols"]:
        futures_paths = sorted(
            (output / "data" / "futures_monthly" / "klines" / symbol).glob("*.zip")
        ) + sorted(
            (output / "data" / "futures_daily" / "klines" / symbol).glob("*.zip")
        )
        index_paths = sorted(
            (
                output
                / "data"
                / "futures_monthly"
                / "indexPriceKlines"
                / symbol
            ).glob("*.zip")
        ) + sorted(
            (
                output
                / "data"
                / "futures_daily"
                / "indexPriceKlines"
                / symbol
            ).glob("*.zip")
        )
        spot_paths = sorted(
            (output / "data" / "spot_monthly" / "klines" / symbol).glob("*.zip")
        ) + sorted(
            (output / "data" / "spot_daily" / "klines" / symbol).glob("*.zip")
        )
        futures = market_adapter.load_market_series(
            futures_paths,
            start,
            end,
        )
        spot = market_adapter.load_market_series(spot_paths, start, end)
        index_price = load_index_series(index_paths, start, end)
        metrics = metrics_adapter.load_metrics(
            sorted((output / "data" / "metrics" / symbol).glob("*.zip")),
            start,
            end,
        )
        frames[symbol] = build_symbol_frame(
            futures,
            spot,
            index_price,
            metrics,
            rules,
        )
    frames = add_cross_market_features(frames)

    candidate_parts = [
        candidate_events(symbol, frame, rules)
        for symbol, frame in frames.items()
    ]
    candidate_parts = [part for part in candidate_parts if not part.empty]
    candidates = (
        pd.concat(candidate_parts, ignore_index=True).sort_values("event_ts")
        if candidate_parts
        else pd.DataFrame()
    )
    routed = classify_and_cooldown(candidates, rules)
    routed.to_csv(output / "routed_events.csv", index=False)
    selected, skips = arbitrate(routed)
    selected.to_csv(output / "selected_episodes.csv", index=False)

    splits = {
        "development": summarize(
            selected,
            evaluation["development_start"],
            evaluation["development_end_exclusive"],
        ),
        "stability": summarize(
            selected,
            evaluation["stability_start"],
            evaluation["stability_end_exclusive"],
        ),
        "july_confirmation": summarize(
            selected,
            evaluation["july_confirmation_start"],
            evaluation["july_confirmation_end_exclusive"],
        ),
        "latest_august_pulse": summarize(
            selected,
            evaluation["latest_august_pulse_start"],
            evaluation["latest_august_pulse_end_exclusive"],
        ),
    }
    routes = cross_split_routes(splits)
    development = splits["development"]
    stability = splits["stability"]
    july = splits["july_confirmation"]
    august = splits["latest_august_pulse"]
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
        "family_stability_frequency": (
            stability["trades_per_day"]
            >= float(gate["minimum_family_stability_trades_per_calendar_day"])
        ),
        "positive_july_confirmation_mean_net": (
            july["mean_net_bps"] is not None and july["mean_net_bps"] > 0.0
        ),
        "july_confirmation_trade_count": (
            july["trades"] >= int(gate["minimum_july_confirmation_trades"])
        ),
        "positive_latest_august_pulse_mean_net": (
            august["mean_net_bps"] is not None
            and august["mean_net_bps"]
            > float(gate["minimum_latest_august_pulse_mean_net_bps"])
        ),
        "latest_august_pulse_trade_count": (
            august["trades"] >= int(gate["minimum_latest_august_pulse_trades"])
        ),
        "symbol_concentration": (
            concentration <= float(gate["maximum_single_symbol_share"])
        ),
    }
    passed = all(checks.values())
    if passed:
        classification = "V21_FAMILY_ADVANCES_TO_FROZEN_NAUTILUS"
        decision = (
            "The fixed co-jump family survived development, year-long stability, July "
            "confirmation and the previously unused August daily pulse. Freeze the "
            "routes for NautilusTrader implementation, while retaining the final "
            "integrated-system requirement of at least one independent trade per day."
        )
    else:
        classification = "V21_COJUMP_ROUTER_REJECTED_OR_UNDERPOWERED"
        survivors = [
            route
            for route, evidence in routes.items()
            if evidence["positive_across_all_declared_splits"]
        ]
        decision = (
            "The co-jump router failed at least one predeclared family gate. "
            f"Cross-split-positive routes: {survivors}. Do not tune jump/state "
            "thresholds; retain only those routes and move to another independent "
            "mechanism."
        )
    payload = {
        "schema": "candidate-15-v21-summary-v1",
        "classification": classification,
        "advance_to_nautilus": passed,
        "prior_only_bipower_jump_detector": True,
        "total_screening_cost_bps": total_cost(rules) * 10_000.0,
        "candidate_events": len(candidates.index),
        "routed_events": len(routed.index),
        "selected_events": len(selected.index),
        "arbitration_skips": dict(skips),
        **splits,
        "cross_split_routes": routes,
        "advance_checks": checks,
        "latest_august_pulse_status": evaluation[
            "latest_august_pulse_status"
        ],
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
