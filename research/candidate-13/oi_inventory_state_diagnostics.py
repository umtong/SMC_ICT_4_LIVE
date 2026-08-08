#!/usr/bin/env python3
"""Causal OI-state diagnostic for Candidate 13's exposed v4 core trades.

This module is deliberately observational. It reuses the Candidate 05
positioning contract: official Binance USD-M five-minute metrics archives,
checksum verification, a full five-minute publication delay, and backward-only
joins at the Candidate 13 confirmation timestamp.

It does not simulate orders, alter PnL, or call the exposed sample an OOS test.
The only policy-like counterfactual is the already-defined Candidate 05 rule
that CHoCH open-interest expansion above 0.10% is not a positioning reset.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import urllib.request
from typing import Any, Iterable

import pandas as pd

BASE = "https://data.binance.vision/data/futures/um/daily"
METRICS_COLUMNS = (
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
)
MAX_NONMATERIAL_OI_EXPANSION_15M = 0.001


@dataclass(frozen=True, slots=True)
class RawEvidence:
    symbol: str
    day: str
    archive: str
    checksum: str
    size_bytes: int
    sha256: str


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def download_metrics(symbol: str, day: date, cache: Path) -> tuple[Path, RawEvidence]:
    stamp = day.isoformat()
    filename = f"{symbol}-metrics-{stamp}.zip"
    url = f"{BASE}/metrics/{symbol}/{filename}"
    directory = cache / "metrics" / symbol
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / filename
    checksum_path = directory / f"{filename}.CHECKSUM"
    if not archive.exists():
        urllib.request.urlretrieve(url, archive)
    if not checksum_path.exists():
        urllib.request.urlretrieve(url + ".CHECKSUM", checksum_path)
    expected = checksum_path.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = sha256_file(archive)
    if actual != expected:
        raise RuntimeError(f"checksum mismatch for {archive}: {actual} != {expected}")
    return archive, RawEvidence(
        symbol=symbol,
        day=stamp,
        archive=str(archive),
        checksum=str(checksum_path),
        size_bytes=archive.stat().st_size,
        sha256=actual,
    )


def archive_day(path: Path) -> str:
    match = re.search(r"metrics-(\d{4}-\d{2}-\d{2})\.zip$", path.name)
    if match is None:
        raise RuntimeError(f"cannot identify metrics archive day: {path}")
    return match.group(1)


def as_utc(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values, utc=True, errors="raise").astype("datetime64[ns, UTC]")


def read_metrics(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, compression="zip")
    required = {"create_time", "symbol", *METRICS_COLUMNS}
    if not required.issubset(raw.columns):
        raise RuntimeError(f"unexpected metrics schema in {path}: {list(raw.columns)}")
    raw["metrics_create_time"] = as_utc(raw["create_time"])
    raw["metrics_observed_time"] = as_utc(
        raw["metrics_create_time"] + pd.Timedelta(minutes=5),
    )
    for column in METRICS_COLUMNS:
        raw[column] = pd.to_numeric(raw[column], errors="raise")
    raw["_source_day"] = archive_day(path)
    return raw[[
        "metrics_create_time",
        "metrics_observed_time",
        "_source_day",
        *METRICS_COLUMNS,
    ]].sort_values("metrics_create_time", kind="stable")


def rows_identical(group: pd.DataFrame) -> bool:
    return all(group[column].nunique(dropna=False) <= 1 for column in METRICS_COLUMNS)


def deduplicate_metrics(metrics: pd.DataFrame) -> pd.DataFrame:
    frame = metrics.sort_values("metrics_observed_time", kind="stable").copy()
    selected: list[pd.Series] = []
    for timestamp, group in frame.groupby("metrics_observed_time", sort=True):
        if len(group) == 1:
            selected.append(group.iloc[0])
            continue
        create_days = group["metrics_create_time"].dt.strftime("%Y-%m-%d")
        owners = group.loc[create_days == group["_source_day"].astype(str)]
        if len(owners) == 1:
            selected.append(owners.iloc[0])
            continue
        candidates = owners if len(owners) > 1 else group
        if rows_identical(candidates):
            selected.append(candidates.iloc[-1])
            continue
        conflicts = [
            column for column in METRICS_COLUMNS
            if candidates[column].nunique(dropna=False) > 1
        ]
        raise RuntimeError(
            "conflicting metrics observation without canonical archive owner at "
            f"{timestamp}: {conflicts}",
        )
    result = pd.DataFrame(selected).sort_values("metrics_observed_time", kind="stable")
    if result["metrics_observed_time"].duplicated().any():
        raise RuntimeError("duplicate causal metrics observation timestamp")
    return result.reset_index(drop=True)


def add_positioning_features(metrics: pd.DataFrame) -> pd.DataFrame:
    frame = deduplicate_metrics(metrics)
    frame["oi_change_5m"] = frame["sum_open_interest"].pct_change(1, fill_method=None)
    frame["oi_change_15m"] = frame["sum_open_interest"].pct_change(3, fill_method=None)
    frame["oi_change_30m"] = frame["sum_open_interest"].pct_change(6, fill_method=None)
    frame["oi_value_change_15m"] = frame["sum_open_interest_value"].pct_change(3, fill_method=None)
    frame["top_position_ratio_change_15m"] = frame[
        "sum_toptrader_long_short_ratio"
    ].pct_change(3, fill_method=None)
    frame["account_ratio_change_15m"] = frame[
        "count_long_short_ratio"
    ].pct_change(3, fill_method=None)
    frame["taker_ratio_change_15m"] = frame[
        "sum_taker_long_short_vol_ratio"
    ].pct_change(3, fill_method=None)
    return frame


def daterange(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def load_symbol_metrics(
    *,
    symbol: str,
    intervals: list[tuple[pd.Timestamp, pd.Timestamp]],
    cache: Path,
) -> tuple[pd.DataFrame, list[RawEvidence]]:
    days: set[date] = set()
    for sweep, confirmation in intervals:
        start = sweep.date() - timedelta(days=1)
        end = confirmation.date()
        days.update(daterange(start, end))
    frames: list[pd.DataFrame] = []
    evidence: list[RawEvidence] = []
    for day in sorted(days):
        archive, item = download_metrics(symbol, day, cache)
        frames.append(read_metrics(archive))
        evidence.append(item)
    if not frames:
        raise RuntimeError(f"no metrics requested for {symbol}")
    return add_positioning_features(pd.concat(frames, ignore_index=True)), evidence


def latest_at(frame: pd.DataFrame, timestamp: pd.Timestamp) -> pd.Series | None:
    eligible = frame.loc[frame["metrics_observed_time"] <= timestamp]
    if eligible.empty:
        return None
    return eligible.iloc[-1]


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def ratio_change(end_value: Any, start_value: Any) -> float | None:
    end = finite_number(end_value)
    start = finite_number(start_value)
    if end is None or start is None or start == 0.0:
        return None
    return end / start - 1.0


def sign_label(value: float | None) -> str:
    if value is None:
        return "UNRESOLVED"
    if value < 0.0:
        return "DECREASED"
    if value > 0.0:
        return "INCREASED"
    return "UNCHANGED"


def classify_trade(trade: dict[str, Any], frame: pd.DataFrame) -> dict[str, Any]:
    sweep = pd.to_datetime(int(trade["sweep_ts_ns"]), unit="ns", utc=True)
    confirmation = pd.to_datetime(int(trade["confirmation_ts_ns"]), unit="ns", utc=True)
    before = latest_at(frame, sweep)
    at_confirmation = latest_at(frame, confirmation)
    if before is None or at_confirmation is None:
        capture = False
        event_oi_change = None
        event_oi_value_change = None
        end_create_time = None
    else:
        end_create_time = at_confirmation["metrics_create_time"]
        capture = bool(end_create_time >= sweep)
        event_oi_change = ratio_change(
            at_confirmation["sum_open_interest"],
            before["sum_open_interest"],
        )
        event_oi_value_change = ratio_change(
            at_confirmation["sum_open_interest_value"],
            before["sum_open_interest_value"],
        )

    oi_15m = None if at_confirmation is None else finite_number(
        at_confirmation["oi_change_15m"],
    )
    reset_compatible = bool(
        capture and oi_15m is not None
        and oi_15m <= MAX_NONMATERIAL_OI_EXPANSION_15M
    )
    if not capture:
        oi_state = "UNRESOLVED_NO_POST_SWEEP_METRIC"
    elif event_oi_change is None or oi_15m is None:
        oi_state = "UNRESOLVED_MISSING_CHANGE"
    elif event_oi_change < 0.0 and reset_compatible:
        oi_state = "DELEVERAGING_RESET"
    elif event_oi_change >= 0.0 and reset_compatible:
        oi_state = "NONEXPANDING_CHOCH_AFTER_FRESH_EVENT"
    elif event_oi_change > 0.0 and not reset_compatible:
        oi_state = "FRESH_INVENTORY_SPONSORSHIP"
    else:
        oi_state = "MIXED_POSITIONING"

    def causal_value(column: str) -> float | None:
        return None if at_confirmation is None else finite_number(at_confirmation[column])

    result = dict(trade)
    result.update({
        "sweep_time": sweep.isoformat(),
        "confirmation_time": confirmation.isoformat(),
        "confirmation_lag_minutes": (confirmation - sweep).total_seconds() / 60.0,
        "metrics_capture": capture,
        "metrics_create_time_at_confirmation": (
            None if end_create_time is None else pd.Timestamp(end_create_time).isoformat()
        ),
        "metrics_observed_time_at_confirmation": (
            None if at_confirmation is None
            else pd.Timestamp(at_confirmation["metrics_observed_time"]).isoformat()
        ),
        "metrics_age_seconds": (
            None if at_confirmation is None
            else (confirmation - at_confirmation["metrics_observed_time"]).total_seconds()
        ),
        "event_oi_change": event_oi_change,
        "event_oi_value_change": event_oi_value_change,
        "event_oi_direction": sign_label(event_oi_change),
        "oi_change_5m": causal_value("oi_change_5m"),
        "oi_change_15m": oi_15m,
        "oi_change_30m": causal_value("oi_change_30m"),
        "oi_value_change_15m": causal_value("oi_value_change_15m"),
        "top_position_ratio_change_15m": causal_value("top_position_ratio_change_15m"),
        "account_ratio_change_15m": causal_value("account_ratio_change_15m"),
        "taker_ratio_change_15m": causal_value("taker_ratio_change_15m"),
        "reset_compatible_c05": reset_compatible,
        "oi_state": oi_state,
    })
    return result


def summarize(records: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record.get(key))].append(record)
    result: dict[str, Any] = {}
    for label, rows in sorted(grouped.items()):
        pnls = [float(row["pnl"]) for row in rows]
        wins = [value for value in pnls if value > 0.0]
        losses = [value for value in pnls if value < 0.0]
        result[label] = {
            "trades": len(rows),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(rows) if rows else 0.0,
            "net_pnl_usdt": sum(pnls),
            "gross_profit_usdt": sum(wins),
            "gross_loss_usdt": -sum(losses),
            "payoff_ratio": (
                (sum(wins) / len(wins)) / (-sum(losses) / len(losses))
                if wins and losses else (math.inf if wins else 0.0)
            ),
            "weeks": sorted({int(row["week"]) for row in rows}),
            "scenario_ids": [str(row["scenario_id"]) for row in rows],
        }
    return result


def policy_counterfactual(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply only the pre-existing Candidate 05 reset predicate to FAR trades.

    AAC trades remain untouched because the predicate was designed for early
    reversal participation, not true-acceptance continuation.
    """
    retained = [
        row for row in records
        if row["scenario"] != "FAR" or bool(row["reset_compatible_c05"])
    ]
    retained_ids = {str(row["scenario_id"]) for row in retained}
    rejected = [row for row in records if str(row["scenario_id"]) not in retained_ids]
    summary = summarize(retained, "scenario")
    pnls = [float(row["pnl"]) for row in retained]
    return {
        "name": "CANDIDATE05_POSITIONING_RESET_COMPATIBLE_FAR",
        "rule": (
            "retain AAC; retain FAR only when a post-sweep causal metrics row is "
            "available and confirmation oi_change_15m <= 0.001"
        ),
        "retained_trades": len(retained),
        "rejected_trades": len(rejected),
        "retained_wins": sum(float(row["pnl"]) > 0.0 for row in retained),
        "retained_losses": sum(float(row["pnl"]) < 0.0 for row in retained),
        "retained_net_pnl_usdt": sum(pnls),
        "rejected_net_pnl_usdt": sum(float(row["pnl"]) for row in rejected),
        "retained_scenario_ids": [row["scenario_id"] for row in retained],
        "rejected_scenario_ids": [row["scenario_id"] for row in rejected],
        "by_scenario": summary,
    }


def markdown_report(result: dict[str, Any]) -> str:
    lines = [
        "# Candidate 13 V13 — causal OI-state diagnostic",
        "",
        "This is an **exposed-development diagnostic**, not OOS evidence.",
        "Official Binance USD-M five-minute metrics are checksum verified and become observable only five minutes after `create_time`.",
        "",
        "## Coverage",
        "",
        f"- trades: {result['coverage']['trades']}",
        f"- post-sweep causal captures: {result['coverage']['post_sweep_captures']}",
        f"- unresolved: {result['coverage']['unresolved']}",
        "",
        "## Outcome by OI state",
        "",
        "| state | trades | wins | losses | win rate | net PnL USDT | payoff |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for state, stats in result["summaries"]["oi_state"].items():
        payoff = stats["payoff_ratio"]
        payoff_text = "inf" if math.isinf(payoff) else f"{payoff:.3f}"
        lines.append(
            f"| {state} | {stats['trades']} | {stats['wins']} | {stats['losses']} | "
            f"{stats['win_rate']:.2%} | {stats['net_pnl_usdt']:.2f} | {payoff_text} |",
        )
    policy = result["preexisting_policy_counterfactual"]
    lines.extend([
        "",
        "## Pre-existing Candidate 05 reset predicate",
        "",
        f"- retained: {policy['retained_trades']} trades, {policy['retained_wins']} wins, {policy['retained_losses']} losses",
        f"- retained net PnL: {policy['retained_net_pnl_usdt']:.2f} USDT",
        f"- rejected: {policy['rejected_trades']} trades, net {policy['rejected_net_pnl_usdt']:.2f} USDT",
        "",
        "No threshold was fitted to Candidate 13 outcomes. The 0.10% threshold is reused unchanged from Candidate 05's positioning-reset predicate.",
        "",
    ])
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--evidence-json", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fixture = json.loads(args.events.read_text(encoding="utf-8"))
    trades = list(fixture.get("trades", []))
    if fixture.get("role") != "EXPOSED_DEVELOPMENT_DIAGNOSTIC_ONLY":
        raise RuntimeError("fixture role must remain exposed-development diagnostic only")
    if not trades:
        raise RuntimeError("no trades in fixture")

    intervals_by_symbol: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]] = defaultdict(list)
    for trade in trades:
        sweep = pd.to_datetime(int(trade["sweep_ts_ns"]), unit="ns", utc=True)
        confirmation = pd.to_datetime(int(trade["confirmation_ts_ns"]), unit="ns", utc=True)
        if confirmation < sweep:
            raise RuntimeError(f"confirmation precedes sweep: {trade['scenario_id']}")
        intervals_by_symbol[str(trade["symbol"])].append((sweep, confirmation))

    metrics_by_symbol: dict[str, pd.DataFrame] = {}
    evidence: list[RawEvidence] = []
    for symbol, intervals in sorted(intervals_by_symbol.items()):
        frame, symbol_evidence = load_symbol_metrics(
            symbol=symbol,
            intervals=intervals,
            cache=args.cache,
        )
        metrics_by_symbol[symbol] = frame
        evidence.extend(symbol_evidence)

    records = [
        classify_trade(trade, metrics_by_symbol[str(trade["symbol"])])
        for trade in trades
    ]
    captures = sum(bool(row["metrics_capture"]) for row in records)
    result = {
        "schema": "candidate-13-v13-causal-oi-state-diagnostic-v1",
        "evaluation_role": "EXPOSED_DEVELOPMENT_DIAGNOSTIC_ONLY",
        "source_fixture_schema": fixture.get("schema"),
        "causal_contract": {
            "archive": "official Binance USD-M daily metrics",
            "checksum_verified": True,
            "observation_delay_minutes": 5,
            "join_direction": "backward_only",
            "candidate05_reset_threshold_oi_change_15m": MAX_NONMATERIAL_OI_EXPANSION_15M,
        },
        "coverage": {
            "trades": len(records),
            "post_sweep_captures": captures,
            "unresolved": len(records) - captures,
            "coverage_rate": captures / len(records),
        },
        "summaries": {
            "oi_state": summarize(records, "oi_state"),
            "event_oi_direction": summarize(records, "event_oi_direction"),
            "reset_compatible_c05": summarize(records, "reset_compatible_c05"),
            "scenario": summarize(records, "scenario"),
        },
        "preexisting_policy_counterfactual": policy_counterfactual(records),
        "records": records,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.evidence_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=True) + "\n",
        encoding="utf-8",
    )
    args.output_md.write_text(markdown_report(result), encoding="utf-8")
    args.evidence_json.write_text(
        json.dumps([asdict(item) for item in evidence], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "coverage": result["coverage"],
        "oi_state": result["summaries"]["oi_state"],
        "policy": result["preexisting_policy_counterfactual"],
    }, indent=2, sort_keys=True, allow_nan=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
