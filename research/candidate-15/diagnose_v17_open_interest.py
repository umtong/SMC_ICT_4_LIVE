#!/usr/bin/env python3
"""Candidate 15 V17 open-interest state-transition mechanism diagnostic."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
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

import diagnose_v16_index_basis as common

METRIC_COLUMNS = (
    "create_time",
    "symbol",
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
)
EXHAUSTION = "DELEVERAGING_EXHAUSTION_REVERSAL"
BUILDUP = "POSITION_BUILDUP_ACCEPTANCE_CONTINUATION"


def days(start: date, end_exclusive: date) -> Iterable[date]:
    cursor = start
    while cursor < end_exclusive:
        yield cursor
        cursor += timedelta(days=1)


def metric_url(symbol: str, token: str) -> str:
    return (
        "https://data.binance.vision/data/futures/um/daily/metrics/"
        f"{symbol}/{symbol}-metrics-{token}.zip"
    )


def download_metric(task: tuple[str, str, Path]) -> dict[str, Any]:
    symbol, token, destination = task
    url = metric_url(symbol, token)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.stat().st_size < 100:
        request = Request(url, headers={"User-Agent": "SMC-ICT-4-candidate-15-v17"})
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
                        raise RuntimeError("corrupt metrics archive")
                temporary.replace(destination)
                break
            except Exception as exc:
                last = exc
                if attempt == 4:
                    raise RuntimeError(f"download failed {url}: {last}") from exc
                time.sleep(2**attempt)
    payload = destination.read_bytes()
    return {
        "symbol": symbol,
        "date": token,
        "bytes": len(payload),
        "sha256": sha256(payload).hexdigest(),
    }


def read_metric_archive(path: Path) -> pd.DataFrame:
    with ZipFile(path) as archive:
        members = [name for name in archive.namelist() if not name.endswith("/")]
        if len(members) != 1:
            raise RuntimeError(f"unexpected metrics members in {path}: {members}")
        payload = archive.read(members[0])
    frame = pd.read_csv(BytesIO(payload))
    if not set(METRIC_COLUMNS).issubset(frame.columns):
        frame = pd.read_csv(BytesIO(payload), names=METRIC_COLUMNS, header=None)
    else:
        frame = frame.loc[:, METRIC_COLUMNS]
    return frame[pd.to_numeric(frame["create_time"], errors="coerce").notna()].copy()


def load_metrics(paths: list[Path], start: date, end: date) -> pd.DataFrame:
    if not paths:
        raise RuntimeError("no metric archives")
    raw = pd.concat(
        [read_metric_archive(path) for path in paths],
        ignore_index=True,
    )
    raw = raw.drop_duplicates("create_time", keep="last").sort_values("create_time")
    timestamp = pd.to_numeric(raw["create_time"], errors="raise").astype("int64")
    first = int(timestamp.iloc[0])
    if 1_000_000_000 <= first < 10_000_000_000:
        unit = "s"
    elif 1_000_000_000_000 <= first < 10_000_000_000_000:
        unit = "ms"
    elif 1_000_000_000_000_000 <= first < 10_000_000_000_000_000:
        unit = "us"
    else:
        raise RuntimeError(f"unsupported metric timestamp {first}")
    index = pd.to_datetime(timestamp, unit=unit, utc=True)
    output = pd.DataFrame(index=index)
    for column in METRIC_COLUMNS[2:]:
        output[column] = pd.to_numeric(raw[column], errors="raise").to_numpy()
    output = output[~output.index.duplicated(keep="last")].sort_index()
    lower, upper = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    output = output[(output.index > lower) & (output.index <= upper)]
    expected = int((upper - lower).total_seconds() // 300)
    if len(output.index) / max(expected, 1) < 0.97:
        raise RuntimeError(
            f"insufficient metrics coverage: {len(output.index)}/{expected}",
        )
    return output


def state_events(
    symbol: str,
    futures: pd.DataFrame,
    index_price: pd.DataFrame,
    metrics: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    joined = futures.join(
        index_price.rename(columns={"close": "index_close"}),
        how="inner",
    ).join(metrics, how="inner")
    close = joined["close"].astype(float)
    index_close = joined["index_close"].astype(float)
    quote_volume = joined["quote_volume"].astype(float)
    open_interest = joined["sum_open_interest"].astype(float).replace(0.0, np.nan)
    taker_ratio = (
        joined["sum_taker_long_short_vol_ratio"].astype(float).clip(lower=1e-8)
    )
    taker_pressure = np.tanh(0.5 * np.log(taker_ratio))
    returns = close.pct_change()
    index_returns = index_close.pct_change()
    oi_change = np.log(open_interest).diff()
    volume_log = np.log1p(quote_volume)
    window = int(rules["rolling_prior_bars"])
    minimum = int(rules["minimum_prior_bars"])
    return_z, _, _ = common.rolling_z(
        returns,
        returns.shift(1),
        window,
        minimum,
    )
    volume_z, _, _ = common.rolling_z(
        volume_log,
        volume_log.shift(1),
        window,
        minimum,
    )
    oi_z, oi_mean, oi_standard = common.rolling_z(
        oi_change,
        oi_change.shift(1),
        window,
        minimum,
    )
    direction = np.sign(returns)
    directional_event_flow = direction * taker_pressure
    context = (
        (return_z.abs() >= float(rules["absolute_return_z_min"]))
        & (volume_z >= float(rules["quote_volume_z_min"]))
        & (
            directional_event_flow
            >= float(rules["directional_event_taker_pressure_min"])
        )
    )
    deleveraging = context & (
        oi_z <= float(rules["open_interest_drop_z_max"])
    )
    buildup = context & (
        oi_z >= float(rules["open_interest_build_z_min"])
    )
    state_candidate = deleveraging | buildup
    confirmation_close = close.shift(-1)
    confirmation_index = index_close.shift(-1)
    confirmation_price_follow = direction * (confirmation_close / close - 1.0)
    confirmation_oi_change = oi_change.shift(-1)
    confirmation_oi_z = (confirmation_oi_change - oi_mean) / oi_standard
    confirmation_flow = direction * taker_pressure.shift(-1)
    index_follow_ratio = (
        direction
        * (confirmation_index / index_close - 1.0)
        / returns.abs().replace(0.0, np.nan)
    )
    output = pd.DataFrame(index=joined.index)
    output["symbol"] = symbol
    output["event_direction"] = direction
    output["return_z"] = return_z
    output["volume_z"] = volume_z
    output["open_interest_z"] = oi_z
    output["directional_event_taker_pressure"] = directional_event_flow
    output["confirmation_price_follow"] = confirmation_price_follow
    output["confirmation_open_interest_z"] = confirmation_oi_z
    output["confirmation_taker_pressure"] = confirmation_flow
    output["index_follow_ratio"] = index_follow_ratio
    output["entry_ts"] = output.index + pd.Timedelta(minutes=5)
    output["entry_price"] = confirmation_close
    output["return_reversal"] = -direction * (
        close.shift(-13) / confirmation_close - 1.0
    )
    output["return_continuation"] = direction * (
        close.shift(-25) / confirmation_close - 1.0
    )
    output["deleveraging_state"] = deleveraging
    output["buildup_state"] = buildup
    output["event_score"] = (
        return_z.abs()
        * np.maximum(volume_z, 0.0)
        * oi_z.abs()
        * np.maximum(directional_event_flow, 0.0)
    )
    output = output[state_candidate].replace([np.inf, -np.inf], np.nan)
    output = output.dropna(
        subset=[
            "event_direction",
            "return_z",
            "volume_z",
            "open_interest_z",
            "directional_event_taker_pressure",
            "confirmation_price_follow",
            "confirmation_open_interest_z",
            "confirmation_taker_pressure",
            "index_follow_ratio",
            "return_reversal",
            "return_continuation",
            "event_score",
        ]
    )
    return output


def add_breadth(
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
        values = returns.loc[stamp].dropna().to_numpy(dtype=float)
        breadth.append(
            float(np.mean(np.sign(values) == float(row["event_direction"])))
            if len(values)
            else float("nan")
        )
    output = events.copy()
    output["cross_market_breadth"] = breadth
    return output.dropna(subset=["cross_market_breadth"])


def classify_and_cooldown(
    events: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    exhaustion = (
        events["deleveraging_state"].astype(bool)
        & (events["confirmation_price_follow"] <= 0.0)
        & (
            events["confirmation_open_interest_z"]
            >= float(rules["confirmation_open_interest_z_min"])
        )
        & (
            events["confirmation_taker_pressure"]
            <= float(rules["confirmation_opposing_taker_pressure_max"])
        )
    )
    acceptance = (
        events["buildup_state"].astype(bool)
        & (events["confirmation_price_follow"] > 0.0)
        & (
            events["confirmation_open_interest_z"]
            >= float(rules["confirmation_open_interest_z_min"])
        )
        & (
            events["confirmation_taker_pressure"]
            >= float(rules["confirmation_aligned_taker_pressure_min"])
        )
        & (
            events["index_follow_ratio"]
            >= float(rules["index_follow_ratio_min"])
        )
        & (
            events["cross_market_breadth"]
            >= float(rules["cross_market_breadth_min"])
        )
    )
    output = events[exhaustion | acceptance].copy()
    output["route"] = np.where(
        exhaustion[exhaustion | acceptance],
        EXHAUSTION,
        BUILDUP,
    )
    output["horizon_minutes"] = np.where(
        output["route"] == EXHAUSTION,
        int(rules["reversal_horizon_minutes"]),
        int(rules["continuation_horizon_minutes"]),
    )
    output["gross_return"] = np.where(
        output["route"] == EXHAUSTION,
        output["return_reversal"],
        output["return_continuation"],
    )
    output["net_return"] = (
        output["gross_return"]
        - float(rules["round_trip_cost_bps"]) / 10_000.0
    )
    exhaustion_quality = (
        -output["open_interest_z"]
        * (-output["confirmation_taker_pressure"]).clip(lower=0.0)
    )
    acceptance_quality = (
        output["open_interest_z"]
        * output["index_follow_ratio"].clip(lower=0.0)
        * output["cross_market_breadth"]
    )
    output["state_quality"] = np.where(
        output["route"] == EXHAUSTION,
        exhaustion_quality,
        acceptance_quality,
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
    for stamp, group in ordered.groupby("entry_ts", sort=True):
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
    ]
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


def render(payload: dict[str, Any]) -> str:
    development = payload["development"]
    evaluation = payload["evaluation"]
    lines = [
        "# Candidate 15 V17 — Open-interest state-transition diagnostic",
        "",
        f"**{payload['classification']}**",
        "",
        "## Development",
        f"- selected trades / day: `{development['trades']} / {development['trades_per_day']}`",
        f"- gross / net mean: `{development['mean_gross_bps']} / {development['mean_net_bps']}` bp",
        f"- win rate / payoff: `{development['win_rate']} / {development['payoff_ratio']}`",
        f"- net t-stat: `{development['net_t_stat']}`",
        "",
        "## Untouched evaluation",
        f"- selected trades / day: `{evaluation['trades']} / {evaluation['trades_per_day']}`",
        f"- gross / net mean: `{evaluation['mean_gross_bps']} / {evaluation['mean_net_bps']}` bp",
        f"- win rate / payoff: `{evaluation['win_rate']} / {evaluation['payoff_ratio']}`",
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
            "This is a mechanism screen rather than synthetic NAV. Any surviving route still requires frozen NautilusTrader orders, 3% current-NAV risk sizing, one global slot and continuous-account validation.",
        )
    )
    return "\n".join(lines) + "\n"


def aggregate_manifest(records: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(
        records,
        key=lambda item: (
            item.get("dataset", "metrics"),
            item["symbol"],
            item.get("month", item.get("date", "")),
        ),
    )
    digest = sha256()
    for record in ordered:
        digest.update(
            (
                f"{record.get('dataset', 'metrics')}|{record['symbol']}|"
                f"{record.get('month', record.get('date', ''))}|"
                f"{record['sha256']}\n"
            ).encode("utf-8")
        )
    by_kind = Counter(record.get("dataset", "metrics") for record in ordered)
    return {
        "schema": "candidate-15-v17-compact-data-manifest-v1",
        "file_count": len(ordered),
        "file_count_by_kind": dict(by_kind),
        "aggregate_sha256": digest.hexdigest(),
        "first_records": ordered[:4],
        "last_records": ordered[-4:],
    }


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = common.load_json(protocol_path)
    data = protocol["data"]
    rules = protocol["fixed_state_rules"]
    evaluation = protocol["evaluation"]
    start = date.fromisoformat(data["start"])
    end = date.fromisoformat(data["end_exclusive"])
    output.mkdir(parents=True, exist_ok=True)
    price_tasks: list[tuple[str, str, str, str, Path]] = []
    for dataset in ("klines", "indexPriceKlines"):
        for symbol in data["symbols"]:
            for month in common.months(start, end):
                token = f"{month.year:04d}-{month.month:02d}"
                destination = (
                    output
                    / "data"
                    / dataset
                    / symbol
                    / f"{symbol}-{data['interval']}-{token}.zip"
                )
                price_tasks.append(
                    (dataset, symbol, data["interval"], token, destination),
                )
    metric_tasks: list[tuple[str, str, Path]] = []
    for symbol in data["symbols"]:
        for day in days(start, end):
            token = day.isoformat()
            destination = (
                output / "data" / "metrics" / symbol / f"{symbol}-metrics-{token}.zip"
            )
            metric_tasks.append((symbol, token, destination))
    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=24) as pool:
        futures = [pool.submit(common.download_one, task) for task in price_tasks]
        futures.extend(pool.submit(download_metric, task) for task in metric_tasks)
        for future in as_completed(futures):
            records.append(future.result())
    common.write_json(output / "data_manifest.json", aggregate_manifest(records))
    index_frames: dict[str, pd.DataFrame] = {}
    futures_frames: dict[str, pd.DataFrame] = {}
    metrics_frames: dict[str, pd.DataFrame] = {}
    for symbol in data["symbols"]:
        futures_frames[symbol] = common.load_series(
            sorted((output / "data" / "klines" / symbol).glob("*.zip")),
            start,
            end,
            True,
        )
        index_frames[symbol] = common.load_series(
            sorted(
                (output / "data" / "indexPriceKlines" / symbol).glob("*.zip"),
            ),
            start,
            end,
            False,
        )
        metrics_frames[symbol] = load_metrics(
            sorted((output / "data" / "metrics" / symbol).glob("*.zip")),
            start,
            end,
        )
    raw_parts = [
        state_events(
            symbol,
            futures_frames[symbol],
            index_frames[symbol],
            metrics_frames[symbol],
            rules,
        )
        for symbol in data["symbols"]
    ]
    raw_events = (
        pd.concat(raw_parts, ignore_index=False).sort_index()
        if raw_parts
        else pd.DataFrame()
    )
    with_breadth = add_breadth(raw_events, index_frames)
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
        "V17_MECHANISM_ADVANCES_TO_FROZEN_NAUTILUS"
        if passed
        else "V17_OPEN_INTEREST_ROUTER_REJECTED_OR_UNDERPOWERED"
    )
    decision = (
        "Freeze the two state transitions and implement event-extreme invalidation plus causal liquidity objectives in the existing NautilusTrader global portfolio runner."
        if passed
        else "The OI state family did not jointly survive costs, stability and independent frequency. Do not tune its numeric thresholds after evaluation; preserve only an independently positive route and move to a different causal family."
    )
    payload = {
        "schema": "candidate-15-v17-summary-v1",
        "classification": classification,
        "advance_to_nautilus": passed,
        "raw_state_events": len(raw_events.index),
        "routed_events": len(routed.index),
        "selected_events": len(selected.index),
        "arbitration_skips": dict(skips),
        "development": development,
        "evaluation": holdout,
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
