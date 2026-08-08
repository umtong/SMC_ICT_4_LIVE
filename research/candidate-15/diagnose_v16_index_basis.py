#!/usr/bin/env python3
"""Candidate 15 V16 futures-index basis state-transition diagnostic."""
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
REVERSAL = "REFLEXIVE_BASIS_REPAIR"
CONTINUATION = "INDEX_CONFIRMED_DISCOVERY"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(path)
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def months(start: date, end: date) -> Iterable[date]:
    cursor = date(start.year, start.month, 1)
    while cursor < end:
        yield cursor
        cursor = date(
            cursor.year + int(cursor.month == 12),
            1 if cursor.month == 12 else cursor.month + 1,
            1,
        )


def archive_url(dataset: str, symbol: str, interval: str, token: str) -> str:
    return (
        "https://data.binance.vision/data/futures/um/monthly/"
        f"{dataset}/{symbol}/{interval}/{symbol}-{interval}-{token}.zip"
    )


def download_one(task: tuple[str, str, str, str, Path]) -> dict[str, Any]:
    dataset, symbol, interval, token, destination = task
    url = archive_url(dataset, symbol, interval, token)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.stat().st_size < 100:
        request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-15-v16"})
        last: Exception | None = None
        for attempt in range(5):
            try:
                with urlopen(request, timeout=90) as response:  # noqa: S310 fixed host
                    payload = response.read()
                if len(payload) < 100:
                    raise RuntimeError(f"unexpectedly small response from {url}")
                temporary = destination.with_suffix(".zip.tmp")
                temporary.write_bytes(payload)
                with ZipFile(temporary) as archive:
                    if archive.testzip() is not None:
                        raise RuntimeError("corrupt archive")
                temporary.replace(destination)
                break
            except Exception as exc:
                last = exc
                if attempt == 4:
                    raise RuntimeError(f"download failed {url}: {last}") from exc
                time.sleep(2**attempt)
    payload = destination.read_bytes()
    return {
        "dataset": dataset,
        "symbol": symbol,
        "month": token,
        "url": url,
        "path": str(destination),
        "bytes": len(payload),
        "sha256": sha256(payload).hexdigest(),
    }


def read_zip(path: Path) -> pd.DataFrame:
    with ZipFile(path) as archive:
        members = [name for name in archive.namelist() if not name.endswith("/")]
        if len(members) != 1:
            raise RuntimeError(f"unexpected archive {path}: {members}")
        payload = archive.read(members[0])
    frame = pd.read_csv(BytesIO(payload))
    if not set(COLUMNS).issubset(frame.columns):
        frame = pd.read_csv(BytesIO(payload), names=COLUMNS, header=None)
    else:
        frame = frame.loc[:, COLUMNS]
    return frame[pd.to_numeric(frame["open_time"], errors="coerce").notna()].copy()


def load_series(
    paths: list[Path],
    start: date,
    end: date,
    with_volume: bool,
) -> pd.DataFrame:
    if not paths:
        raise RuntimeError("no archives found")
    raw = pd.concat([read_zip(path) for path in paths], ignore_index=True)
    raw = raw.drop_duplicates("open_time", keep="last").sort_values("open_time")
    timestamps = pd.to_numeric(raw["open_time"], errors="raise").astype("int64")
    first = int(timestamps.iloc[0])
    if 1_000_000_000_000 <= first < 10_000_000_000_000:
        unit = "ms"
    elif 1_000_000_000_000_000 <= first < 10_000_000_000_000_000:
        unit = "us"
    else:
        raise RuntimeError(f"unsupported timestamp magnitude {first}")
    index = pd.to_datetime(timestamps, unit=unit, utc=True) + pd.Timedelta(minutes=5)
    result = pd.DataFrame(index=index)
    result["close"] = pd.to_numeric(raw["close"], errors="raise").to_numpy()
    if with_volume:
        result["quote_volume"] = pd.to_numeric(
            raw["quote_volume"], errors="raise",
        ).to_numpy()
    result = result[~result.index.duplicated(keep="last")].sort_index()
    lower, upper = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    result = result[(result.index > lower) & (result.index <= upper)]
    expected = int((upper - lower).total_seconds() // 300)
    if len(result.index) / max(expected, 1) < 0.995:
        raise RuntimeError(f"insufficient archive coverage: {len(result.index)}/{expected}")
    return result


def rolling_z(
    current: pd.Series,
    prior: pd.Series,
    window: int,
    minimum: int,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    mean = prior.rolling(window, min_periods=minimum).mean()
    std = prior.rolling(window, min_periods=minimum).std(ddof=0).replace(0.0, np.nan)
    return (current - mean) / std, mean, std


def symbol_events(
    symbol: str,
    futures: pd.DataFrame,
    index_price: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    joined = futures.join(
        index_price.rename(columns={"close": "index_close"}),
        how="inner",
    )
    close = joined["close"].astype(float)
    index_close = joined["index_close"].astype(float)
    basis = close / index_close - 1.0
    futures_return = close.pct_change()
    index_return = index_close.pct_change()
    relative_return = futures_return - index_return
    volume_log = np.log1p(joined["quote_volume"].astype(float))
    window = int(rules["rolling_prior_bars"])
    minimum = int(rules["minimum_prior_bars"])
    basis_z, basis_mean, _ = rolling_z(basis, basis.shift(1), window, minimum)
    relative_z, _, _ = rolling_z(
        relative_return,
        relative_return.shift(1),
        window,
        minimum,
    )
    volume_z, _, _ = rolling_z(volume_log, volume_log.shift(1), window, minimum)
    _, _, return_sigma = rolling_z(
        futures_return,
        futures_return.shift(1),
        window,
        minimum,
    )
    event = (
        (basis_z.abs() >= float(rules["absolute_basis_z_min"]))
        & (relative_z.abs() >= float(rules["absolute_relative_return_z_min"]))
        & (volume_z >= float(rules["futures_volume_z_min"]))
        & (
            futures_return.abs()
            >= float(rules["absolute_return_sigma_min"]) * return_sigma
        )
    )
    event_direction = np.sign(futures_return)
    confirmation_close = close.shift(-1)
    confirmation_index = index_close.shift(-1)
    confirmation_basis = basis.shift(-1)
    basis_distance = (basis - basis_mean).abs().replace(0.0, np.nan)
    basis_repair = 1.0 - (
        (confirmation_basis - basis_mean).abs() / basis_distance
    )
    index_follow = (
        event_direction
        * (confirmation_index / index_close - 1.0)
        / futures_return.abs().replace(0.0, np.nan)
    )
    futures_follow = event_direction * (confirmation_close / close - 1.0)
    output = pd.DataFrame(index=joined.index)
    output["symbol"] = symbol
    output["event_direction"] = event_direction
    output["basis_z"] = basis_z
    output["relative_z"] = relative_z
    output["volume_z"] = volume_z
    output["basis_repair"] = basis_repair
    output["index_follow_ratio"] = index_follow
    output["futures_follow"] = futures_follow
    output["entry_ts"] = output.index + pd.Timedelta(minutes=5)
    output["entry_price"] = confirmation_close
    output["index_entry_price"] = confirmation_index
    output["return_reversal"] = -event_direction * (
        close.shift(-13) / confirmation_close - 1.0
    )
    output["return_continuation"] = event_direction * (
        close.shift(-25) / confirmation_close - 1.0
    )
    output["event_score"] = (
        basis_z.abs() * relative_z.abs() * np.maximum(volume_z, 0.0)
    )
    output = output[event].replace([np.inf, -np.inf], np.nan)
    output = output.dropna(
        subset=[
            "event_direction",
            "basis_z",
            "relative_z",
            "volume_z",
            "basis_repair",
            "index_follow_ratio",
            "futures_follow",
            "return_reversal",
            "return_continuation",
        ]
    )
    cooldown = pd.Timedelta(
        minutes=int(rules["same_symbol_event_cooldown_minutes"]),
    )
    accepted: list[pd.Series] = []
    next_allowed = pd.Timestamp.min.tz_localize("UTC")
    for timestamp, row in output.iterrows():
        if timestamp < next_allowed:
            continue
        accepted.append(row)
        next_allowed = timestamp + cooldown
    return pd.DataFrame(accepted) if accepted else output.iloc[0:0]


def add_cross_market_state(
    events: pd.DataFrame,
    index_frames: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    if events.empty:
        return events
    returns = pd.DataFrame(
        {
            symbol: frame["close"].astype(float).pct_change(2)
            for symbol, frame in index_frames.items()
        }
    )
    breadth: list[float] = []
    for _, row in events.iterrows():
        stamp = pd.Timestamp(row["entry_ts"])
        if stamp not in returns.index:
            breadth.append(float("nan"))
            continue
        direction = float(row["event_direction"])
        values = returns.loc[stamp].dropna().to_numpy(dtype=float)
        breadth.append(
            float(np.mean(np.sign(values) == direction))
            if len(values)
            else float("nan")
        )
    output = events.copy()
    output["cross_market_breadth"] = breadth
    return output.dropna(subset=["cross_market_breadth"])


def classify(events: pd.DataFrame, rules: dict[str, Any]) -> pd.DataFrame:
    repair = (
        (events["basis_repair"] >= float(rules["basis_repair_fraction_min"]))
        & (
            events["index_follow_ratio"]
            <= float(rules["index_follow_ratio_max_for_repair"])
        )
        & (events["futures_follow"] <= 0.0)
    )
    discovery = (
        (
            events["index_follow_ratio"]
            >= float(rules["index_follow_ratio_min_for_discovery"])
        )
        & (
            events["basis_repair"]
            <= float(rules["basis_repair_fraction_max_for_discovery"])
        )
        & (
            events["cross_market_breadth"]
            >= float(rules["cross_market_breadth_min"])
        )
        & (events["futures_follow"] > 0.0)
    )
    output = events[repair | discovery].copy()
    output["route"] = np.where(
        repair[repair | discovery],
        REVERSAL,
        CONTINUATION,
    )
    output["horizon_minutes"] = np.where(
        output["route"] == REVERSAL,
        int(rules["reversal_horizon_minutes"]),
        int(rules["continuation_horizon_minutes"]),
    )
    output["gross_return"] = np.where(
        output["route"] == REVERSAL,
        output["return_reversal"],
        output["return_continuation"],
    )
    output["net_return"] = (
        output["gross_return"]
        - float(rules["round_trip_cost_bps"]) / 10_000.0
    )
    repair_quality = output["basis_repair"].clip(lower=0.0)
    discovery_quality = (
        output["index_follow_ratio"].clip(lower=0.0)
        * output["cross_market_breadth"]
    )
    output["state_quality"] = np.where(
        output["route"] == REVERSAL,
        repair_quality,
        discovery_quality,
    )
    output["rank_score"] = output["event_score"] * output["state_quality"]
    return output


def arbitrate(events: pd.DataFrame) -> tuple[pd.DataFrame, Counter[str]]:
    if events.empty:
        return events, Counter()
    events = events.sort_values(
        ["entry_ts", "rank_score", "symbol"],
        ascending=[True, False, True],
        kind="stable",
    )
    chosen: list[pd.Series] = []
    skips: Counter[str] = Counter()
    free_at = pd.Timestamp.min.tz_localize("UTC")
    for stamp, group in events.groupby("entry_ts", sort=True):
        winner = group.iloc[0]
        skips["SAME_EVENT_LOSER"] += max(0, len(group.index) - 1)
        if stamp < free_at:
            skips["GLOBAL_POSITION_OCCUPIED"] += 1
            continue
        chosen.append(winner)
        free_at = stamp + pd.Timedelta(
            minutes=int(winner["horizon_minutes"]),
        )
    if not chosen:
        return events.iloc[0:0], skips
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
    ]
    days = int((upper - lower).total_seconds() // 86_400)
    if sample.empty:
        return {
            "start": start,
            "end_exclusive": end,
            "calendar_days": days,
            "trades": 0,
            "trades_per_day": 0.0,
            "mean_gross_bps": None,
            "mean_net_bps": None,
            "net_t_stat": None,
            "win_rate": None,
            "positive_month_share": 0.0,
            "positive_months": 0,
            "active_months": 0,
            "route_stats": {},
            "symbol_counts": {},
        }
    monthly = (
        sample.set_index("entry_ts")["net_return"].resample("MS").mean().dropna()
    )
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
        "calendar_days": days,
        "trades": len(sample.index),
        "trades_per_day": len(sample.index) / max(days, 1),
        "mean_gross_bps": float(sample["gross_return"].mean() * 10_000.0),
        "mean_net_bps": float(sample["net_return"].mean() * 10_000.0),
        "net_t_stat": t_stat(sample["net_return"]),
        "win_rate": float((sample["net_return"] > 0.0).mean()),
        "positive_month_share": float((monthly > 0.0).mean()),
        "positive_months": int((monthly > 0.0).sum()),
        "active_months": len(monthly.index),
        "route_stats": route_stats,
        "symbol_counts": dict(Counter(sample["symbol"].astype(str))),
    }


def render(payload: dict[str, Any]) -> str:
    development = payload["development"]
    evaluation = payload["evaluation"]
    lines = [
        "# Candidate 15 V16 — Futures-index basis state-transition diagnostic",
        "",
        f"**{payload['classification']}**",
        "",
        "## Development",
        f"- trades / day: `{development['trades']} / {development['trades_per_day']}`",
        f"- gross / net mean: `{development['mean_gross_bps']} / {development['mean_net_bps']}` bp",
        f"- net t-stat: `{development['net_t_stat']}`",
        "",
        "## Untouched evaluation",
        f"- trades / day: `{evaluation['trades']} / {evaluation['trades_per_day']}`",
        f"- gross / net mean: `{evaluation['mean_gross_bps']} / {evaluation['mean_net_bps']}` bp",
        f"- win rate: `{evaluation['win_rate']}`",
        f"- net t-stat: `{evaluation['net_t_stat']}`",
        f"- positive months: `{evaluation['positive_months']} / {evaluation['active_months']}`",
        f"- route stats: `{evaluation['route_stats']}`",
        f"- symbol counts: `{evaluation['symbol_counts']}`",
        "",
        "## Advance checks",
    ]
    lines.extend(
        f"- {name}: `{value}`"
        for name, value in payload["advance_checks"].items()
    )
    lines.extend(
        (
            "",
            "## Decision",
            payload["decision"],
            "",
            "This is a causal mechanism screen, not synthesized account NAV. A surviving route still requires frozen NautilusTrader orders, risk sizing and continuous NAV validation.",
        )
    )
    return "\n".join(lines) + "\n"


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = load_json(protocol_path)
    data = protocol["data"]
    rules = protocol["fixed_state_rules"]
    evaluation = protocol["evaluation"]
    start = date.fromisoformat(data["start"])
    end = date.fromisoformat(data["end_exclusive"])
    output.mkdir(parents=True, exist_ok=True)
    tasks: list[tuple[str, str, str, str, Path]] = []
    for dataset in ("klines", "indexPriceKlines"):
        for symbol in data["symbols"]:
            for month in months(start, end):
                token = f"{month.year:04d}-{month.month:02d}"
                path = (
                    output
                    / "data"
                    / dataset
                    / symbol
                    / f"{symbol}-{data['interval']}-{token}.zip"
                )
                tasks.append((dataset, symbol, data["interval"], token, path))
    manifest: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(download_one, task) for task in tasks]
        for future in as_completed(futures):
            manifest.append(future.result())
    write_json(
        output / "data_manifest.json",
        {
            "schema": "candidate-15-v16-data-v1",
            "files": sorted(
                manifest,
                key=lambda item: (
                    item["dataset"],
                    item["symbol"],
                    item["month"],
                ),
            ),
        },
    )
    index_frames: dict[str, pd.DataFrame] = {}
    futures_frames: dict[str, pd.DataFrame] = {}
    for symbol in data["symbols"]:
        futures_paths = sorted(
            (output / "data" / "klines" / symbol).glob("*.zip"),
        )
        index_paths = sorted(
            (output / "data" / "indexPriceKlines" / symbol).glob("*.zip"),
        )
        futures_frames[symbol] = load_series(
            futures_paths,
            start,
            end,
            True,
        )
        index_frames[symbol] = load_series(
            index_paths,
            start,
            end,
            False,
        )
    event_parts = [
        symbol_events(
            symbol,
            futures_frames[symbol],
            index_frames[symbol],
            rules,
        )
        for symbol in data["symbols"]
    ]
    events = (
        pd.concat(event_parts, ignore_index=False).sort_index()
        if event_parts
        else pd.DataFrame()
    )
    events = add_cross_market_state(events, index_frames)
    routed = classify(events, rules)
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
    holdout = summarize(
        selected,
        evaluation["evaluation_start"],
        evaluation["evaluation_end_exclusive"],
    )
    gate = protocol["advance_gate"]
    concentration = (
        max(holdout["symbol_counts"].values()) / holdout["trades"]
        if holdout["trades"]
        else 1.0
    )
    checks = {
        "positive_development_mean_net": (
            development["mean_net_bps"] is not None
            and development["mean_net_bps"] > 0.0
        ),
        "positive_evaluation_mean_net": (
            holdout["mean_net_bps"] is not None
            and holdout["mean_net_bps"]
            > float(gate["minimum_evaluation_mean_net_bps"])
        ),
        "evaluation_net_t_stat": (
            holdout["net_t_stat"] is not None
            and holdout["net_t_stat"]
            >= float(gate["minimum_evaluation_net_t_stat"])
        ),
        "positive_month_share": (
            holdout["positive_month_share"]
            >= float(gate["minimum_positive_evaluation_month_share"])
        ),
        "independent_frequency": (
            holdout["trades_per_day"]
            >= float(gate["minimum_selected_trades_per_calendar_day"])
        ),
        "symbol_concentration": (
            concentration <= float(gate["maximum_single_symbol_share"])
        ),
    }
    passed = all(checks.values())
    classification = (
        "V16_MECHANISM_ADVANCES_TO_FROZEN_NAUTILUS"
        if passed
        else "V16_INDEX_BASIS_ROUTER_REJECTED_OR_UNDERPOWERED"
    )
    decision = (
        "Freeze both state-transition rules and implement event-extreme invalidation plus index/basis objectives in the existing NautilusTrader global portfolio runner."
        if passed
        else "The basis-transition family did not jointly survive costs, stability and frequency. Do not tune numeric thresholds after this evaluation; preserve only a route with independently positive evidence and move to a different mechanism."
    )
    payload = {
        "schema": "candidate-15-v16-summary-v1",
        "classification": classification,
        "advance_to_nautilus": passed,
        "raw_events": len(events.index),
        "routed_events": len(routed.index),
        "selected_events": len(selected.index),
        "arbitration_skips": dict(skips),
        "development": development,
        "evaluation": holdout,
        "advance_checks": checks,
        "decision": decision,
    }
    write_json(output / "summary.json", payload)
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
