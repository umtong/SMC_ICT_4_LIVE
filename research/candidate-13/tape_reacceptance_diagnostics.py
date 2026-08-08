#!/usr/bin/env python3
"""Causal one-minute tape/re-acceptance diagnostic for Candidate 13 v4 plans.

The diagnostic is deliberately development-only.  It mines three externally
repeated execution ideas without trusting any trader's performance claims:

1. a sweep reversal must re-enter the prior range;
2. aggressive flow should flip and persist, not merely flash on one bar;
3. renewed acceptance beyond the swept extreme belongs to continuation or
   UNRESOLVED, not to a reversal entry.

Every observation is built from checksum-verified Binance USD-M daily 1-minute
klines.  A kline becomes visible only at open_time + one minute.  Joins are
backward/at-time only; no post-confirmation bar is used by the router.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import urllib.request
from typing import Any, Iterable
from zipfile import ZipFile

import pandas as pd

BASE = "https://data.binance.vision/data/futures/um/daily/klines"
COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
)
MINUTE = pd.Timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class RawEvidence:
    symbol: str
    day: str
    archive: str
    checksum: str
    size_bytes: int
    sha256: str
    rows: int


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def download(symbol: str, day: date, cache: Path) -> tuple[Path, Path]:
    stamp = day.isoformat()
    filename = f"{symbol}-1m-{stamp}.zip"
    url = f"{BASE}/{symbol}/1m/{filename}"
    directory = cache / symbol
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / filename
    checksum = directory / f"{filename}.CHECKSUM"
    if not archive.exists():
        urllib.request.urlretrieve(url, archive)
    if not checksum.exists():
        urllib.request.urlretrieve(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = sha256_file(archive)
    if actual != expected:
        raise RuntimeError(f"checksum mismatch for {archive}: {actual} != {expected}")
    return archive, checksum


def timestamp_unit(value: int) -> str:
    if 1_000_000_000_000 <= value < 10_000_000_000_000:
        return "ms"
    if 1_000_000_000_000_000 <= value < 10_000_000_000_000_000:
        return "us"
    raise RuntimeError(f"unsupported timestamp magnitude: {value}")


def read_klines(path: Path) -> pd.DataFrame:
    with ZipFile(path) as archive:
        members = archive.namelist()
        if len(members) != 1:
            raise RuntimeError(f"unexpected archive members: {path}: {members}")
        payload = archive.read(members[0])
    frame = pd.read_csv(BytesIO(payload))
    if set(COLUMNS).issubset(frame.columns):
        frame = frame.loc[:, COLUMNS]
    else:
        frame = pd.read_csv(BytesIO(payload), header=None, names=COLUMNS)
    numeric_time = pd.to_numeric(frame["open_time"], errors="coerce")
    frame = frame.loc[numeric_time.notna()].copy()
    frame["open_time"] = numeric_time.loc[numeric_time.notna()].astype("int64")
    if len(frame.index) not in (1439, 1440, 1441):
        raise RuntimeError(f"unexpected row count {len(frame.index)} for {path}")
    unit = timestamp_unit(int(frame["open_time"].iloc[0]))
    frame["observed_time"] = pd.to_datetime(frame["open_time"], unit=unit, utc=True) + MINUTE
    for column in ("open", "high", "low", "close", "volume", "taker_buy_volume"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame = frame.drop_duplicates("observed_time", keep="last").sort_values("observed_time")
    if (frame["volume"] < 0).any() or (frame["taker_buy_volume"] < 0).any():
        raise RuntimeError(f"negative volume in {path}")
    if (frame["taker_buy_volume"] > frame["volume"] + 1e-9).any():
        raise RuntimeError(f"taker buy volume exceeds total in {path}")
    frame["delta"] = 2.0 * frame["taker_buy_volume"] - frame["volume"]
    frame["signed_flow"] = frame["delta"] / frame["volume"].where(frame["volume"] > 0, 1.0)
    return frame


def daterange(start: date, end: date) -> Iterable[date]:
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


def load_symbol_days(
    symbol: str,
    days: set[date],
    cache: Path,
) -> tuple[pd.DataFrame, list[RawEvidence]]:
    frames: list[pd.DataFrame] = []
    evidence: list[RawEvidence] = []
    for day in sorted(days):
        archive, checksum = download(symbol, day, cache)
        frame = read_klines(archive)
        frames.append(frame)
        evidence.append(
            RawEvidence(
                symbol=symbol,
                day=day.isoformat(),
                archive=str(archive),
                checksum=str(checksum),
                size_bytes=archive.stat().st_size,
                sha256=sha256_file(archive),
                rows=len(frame.index),
            ),
        )
    if not frames:
        raise RuntimeError(f"no days requested for {symbol}")
    merged = pd.concat(frames, ignore_index=True)
    merged = merged.drop_duplicates("observed_time", keep="last").sort_values("observed_time")
    return merged.reset_index(drop=True), evidence


def load_plans(results_root: Path) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    for path in sorted(results_root.glob("W*/submitted_plans.json")):
        week = path.parent.name
        payload = json.loads(path.read_text(encoding="utf-8"))
        for plan in payload.get("plans", []):
            row = dict(plan)
            row["week"] = week
            plans.append(row)
    if not plans:
        raise RuntimeError(f"no submitted plans under {results_root}")
    return plans


def load_outcomes(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, dict[str, Any]] = {}
    for trade in payload["trades"]:
        key = f"W{int(trade['week'])}|{trade['scenario_id']}|{int(trade['confirmation_ts_ns'])}"
        if key in result:
            raise RuntimeError(f"duplicate outcome key: {key}")
        result[key] = trade
    return result


def streak_at_end(values: list[bool]) -> int:
    count = 0
    for value in reversed(values):
        if not value:
            break
        count += 1
    return count


def maximum_streak(values: list[bool]) -> int:
    best = current = 0
    for value in values:
        if value:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def weighted_flow(frame: pd.DataFrame, sign: float) -> float | None:
    volume = float(frame["volume"].sum())
    if volume <= 0:
        return None
    return sign * float(frame["delta"].sum()) / volume


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def plan_key(plan: dict[str, Any]) -> str:
    return f"{plan['week']}|{plan['scenario_id']}|{int(plan['observed_ts_ns'])}"


def classify(plan: dict[str, Any], outcome: dict[str, Any], bars: pd.DataFrame) -> dict[str, Any]:
    details = plan.get("details") or {}
    sweep_ns = int(details["sweep_ts_ns"])
    confirmation_ns = int(plan["observed_ts_ns"])
    sweep = pd.to_datetime(sweep_ns, unit="ns", utc=True)
    confirmation = pd.to_datetime(confirmation_ns, unit="ns", utc=True)
    event = bars.loc[(bars["observed_time"] >= sweep) & (bars["observed_time"] <= confirmation)].copy()
    sign = 1.0 if plan["direction"] == "LONG" else -1.0
    pool = float(details["pool_level"])
    extreme = float(details["sweep_extreme"])
    target = float(plan["target"])

    record: dict[str, Any] = {
        "row_key": plan_key(plan),
        "week": plan["week"],
        "scenario_id": plan["scenario_id"],
        "scenario": plan["scenario"],
        "symbol": plan["symbol"],
        "direction": plan["direction"],
        "sweep_time": sweep.isoformat(),
        "confirmation_time": confirmation.isoformat(),
        "confirmation_lag_minutes": (confirmation - sweep).total_seconds() / 60.0,
        "pool_level": pool,
        "sweep_extreme": extreme,
        "entry": float(plan["entry"]),
        "stop": float(plan["stop"]),
        "target": target,
        "entry_model": details.get("entry_model"),
        "post_leadership_role": details.get("post_leadership_role") or (details.get("market_leadership") or {}).get("reason"),
        "pnl": float(outcome["pnl"]),
        "win": bool(outcome["win"]),
        "event_bar_count": len(event.index),
    }
    if event.empty or event["observed_time"].iloc[-1] != confirmation:
        record.update({"tape_state": "UNRESOLVED_MISSING_CAUSAL_BAR"})
        return record

    closes_trade_side = (sign * (event["close"] - pool) > 0.0).tolist()
    flow_trade_side = (sign * event["signed_flow"] > 0.0).tolist()
    joint = [a and b for a, b in zip(closes_trade_side, flow_trade_side, strict=True)]
    reclaim_positions = [index for index, value in enumerate(closes_trade_side) if value]
    first_reclaim = reclaim_positions[0] if reclaim_positions else None
    if sign > 0:
        beyond_extreme = (event["close"] <= extreme).tolist()
    else:
        beyond_extreme = (event["close"] >= extreme).tolist()
    after_reclaim = slice(first_reclaim, None) if first_reclaim is not None else slice(0, 0)
    event_range = float(event["high"].max() - event["low"].min())
    path = float(event["close"].diff().abs().sum())
    directional_progress = sign * (float(event["close"].iloc[-1]) - float(event["close"].iloc[0]))
    pool_target_distance = sign * (target - pool)
    consumed = sign * (float(event["close"].iloc[-1]) - pool)
    target_space_remaining = (
        (pool_target_distance - consumed) / pool_target_distance
        if pool_target_distance > 0 else None
    )

    record.update({
        "first_reclaim_offset_minutes": None if first_reclaim is None else first_reclaim,
        "ending_trade_side_close_streak": streak_at_end(closes_trade_side),
        "ending_directional_flow_streak": streak_at_end(flow_trade_side),
        "ending_joint_close_flow_streak": streak_at_end(joint),
        "post_reclaim_extreme_reacceptance_max_streak": maximum_streak(beyond_extreme[after_reclaim]),
        "post_reclaim_pool_reacceptance_max_streak": maximum_streak([not value for value in closes_trade_side[after_reclaim]]),
        "event_directional_flow": weighted_flow(event, sign),
        "post_reclaim_directional_flow": (
            None if first_reclaim is None else weighted_flow(event.iloc[first_reclaim:], sign)
        ),
        "final_2m_directional_flow": weighted_flow(event.tail(2), sign),
        "final_3m_directional_flow": weighted_flow(event.tail(3), sign),
        "final_5m_directional_flow": weighted_flow(event.tail(5), sign),
        "confirmation_bar_directional_flow": sign * float(event["signed_flow"].iloc[-1]),
        "sweep_bar_directional_flow": sign * float(event["signed_flow"].iloc[0]),
        "directional_price_efficiency": directional_progress / path if path > 0 else 0.0,
        "event_range": event_range,
        "target_space_remaining_ratio_at_confirmation": target_space_remaining,
    })

    if plan["scenario"] != "FAR":
        state = "NON_FAR_UNCHANGED"
    elif first_reclaim is None:
        state = "UNRESOLVED_NO_RANGE_RECLAIM"
    elif record["post_reclaim_extreme_reacceptance_max_streak"] >= 2:
        state = "CONTINUATION_REACCEPTED_BEYOND_SWEEP_EXTREME"
    elif record["ending_joint_close_flow_streak"] >= 2:
        state = "REVERSAL_RECLAIM_WITH_PERSISTENT_FLOW"
    elif (
        record["ending_trade_side_close_streak"] >= 2
        and finite(record["final_3m_directional_flow"]) is not None
        and float(record["final_3m_directional_flow"]) > 0.0
    ):
        state = "REVERSAL_RECLAIM_WITH_AGGREGATE_FLOW"
    elif (
        finite(record["final_3m_directional_flow"]) is not None
        and float(record["final_3m_directional_flow"]) < 0.0
    ):
        state = "FAILED_ABSORPTION_OR_FLOW_REVERSION"
    else:
        state = "UNRESOLVED_SINGLE_BAR_CONFIRMATION"
    record["tape_state"] = state
    return record


def summarize(records: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        grouped[str(row.get(key))].append(row)
    result: dict[str, Any] = {}
    for label, rows in sorted(grouped.items()):
        pnls = [float(row["pnl"]) for row in rows]
        wins = [value for value in pnls if value > 0]
        losses = [value for value in pnls if value < 0]
        result[label] = {
            "trades": len(rows),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(rows) if rows else 0.0,
            "net_pnl_usdt": sum(pnls),
            "gross_profit_usdt": sum(wins),
            "gross_loss_usdt": -sum(losses),
            "row_keys": [row["row_key"] for row in rows],
        }
    return result


def markdown_report(result: dict[str, Any]) -> str:
    lines = [
        "# Candidate 13 V14 — causal tape/re-acceptance diagnostic",
        "",
        "This is an **exposed-development diagnostic**, not independent validation.",
        "All one-minute bars are checksum verified and become visible at `open_time + 1 minute`.",
        "",
        "## Accounting",
        "",
        f"- submitted plans: {result['coverage']['plans']}",
        f"- matched outcomes: {result['coverage']['matched_outcomes']}",
        f"- causal bar records: {result['coverage']['causal_bar_records']}",
        "",
        "## Tape states",
        "",
        "| State | Trades | Wins | Losses | Net PnL |",
        "|---|---:|---:|---:|---:|",
    ]
    for label, item in result["summaries"]["tape_state"].items():
        lines.append(
            f"| {label} | {item['trades']} | {item['wins']} | {item['losses']} | {item['net_pnl_usdt']:.2f} |",
        )
    lines.extend([
        "",
        "## Interpretation contract",
        "",
        "- Two consecutive closes beyond the swept extreme after reclaim means true re-acceptance; FAR reversal is not authorized.",
        "- Two consecutive closing bars with direction-aligned aggressive flow authorize persistent reversal confirmation.",
        "- A single displacement bar without persistent or aggregate flow remains unresolved.",
        "- These rules are structural sign/streak rules; no PnL-fitted threshold is selected here.",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--outcomes", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--evidence-json", type=Path, required=True)
    args = parser.parse_args()

    submitted_plans = load_plans(args.results_root)
    outcomes = load_outcomes(args.outcomes)
    required_days: dict[str, set[date]] = defaultdict(set)
    matched: list[tuple[dict[str, Any], dict[str, Any]]] = []
    unmatched_submitted_plan_keys: list[str] = []
    for plan in submitted_plans:
        key = plan_key(plan)
        if key not in outcomes:
            unmatched_submitted_plan_keys.append(key)
            continue
        outcome = outcomes[key]
        details = plan.get("details") or {}
        sweep = pd.to_datetime(int(details["sweep_ts_ns"]), unit="ns", utc=True)
        confirmation = pd.to_datetime(int(plan["observed_ts_ns"]), unit="ns", utc=True)
        required_days[plan["symbol"]].update(
            daterange(sweep.date() - timedelta(days=1), confirmation.date()),
        )
        matched.append((plan, outcome))

    frames: dict[str, pd.DataFrame] = {}
    raw_evidence: list[RawEvidence] = []
    for symbol, days in sorted(required_days.items()):
        frames[symbol], evidence = load_symbol_days(symbol, days, args.cache)
        raw_evidence.extend(evidence)

    records = [classify(plan, outcome, frames[plan["symbol"]]) for plan, outcome in matched]
    if len({row["row_key"] for row in records}) != len(records):
        raise RuntimeError("duplicate diagnostic row key")
    result = {
        "schema": "candidate-13-v14-causal-tape-reacceptance-diagnostic-v1",
        "evaluation_role": "EXPOSED_DEVELOPMENT_DIAGNOSTIC_ONLY",
        "causal_contract": {
            "source": "official Binance USD-M daily one-minute klines",
            "checksum_verified": True,
            "observed_time": "open_time + 1 minute",
            "post_confirmation_bars_used": False,
            "router_thresholds": "sign and two-consecutive-bar structural states only",
        },
        "coverage": {
            "submitted_plans_total": len(submitted_plans),
            "plans": len(matched),
            "matched_outcomes": len(matched),
            "unmatched_submitted_plans": len(unmatched_submitted_plan_keys),
            "unmatched_submitted_plan_keys": unmatched_submitted_plan_keys,
            "causal_bar_records": sum(row.get("event_bar_count", 0) > 0 for row in records),
        },
        "records": records,
        "summaries": {
            "tape_state": summarize(records, "tape_state"),
            "post_leadership_role": summarize(records, "post_leadership_role"),
            "entry_model": summarize(records, "entry_model"),
        },
        "success_claim": False,
    }
    args.output_json.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    args.output_md.write_text(markdown_report(result), encoding="utf-8")
    args.evidence_json.write_text(
        json.dumps(
            {"schema": "candidate-13-v14-raw-kline-evidence-v1", "archives": [asdict(item) for item in raw_evidence]},
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["summaries"]["tape_state"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
