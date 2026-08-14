#!/usr/bin/env python3
"""Reconstruct the exact Binance aggTrade sequence around RE1 flow entries.

One-minute taker-buy summaries identify useful candidate auctions but erase the
order in which aggression, penetration and reclaim occurred.  This tool uses
checksum-verified Binance Vision USD-M ``aggTrades`` only for already-generated
causal entry minutes and reconstructs:

* adverse taker quote outside the decision boundary;
* deepest penetration and quote per penetration basis point;
* first penetration-to-reclaim latency;
* adverse and intended signed flow before and after reclaim;
* maximum consecutive adverse/intended aggressor runs;
* post-reclaim price progress through the signal close;
* archive continuity diagnostics.

The result is an event study, not an outcome filter.  Every feature ends at the
plan's completed trigger minute.  Future trade PnL is attached only as a label
for research and is never used to construct the event features.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

import pandas as pd


BASE = "https://data.binance.vision/data/futures/um/daily/aggTrades"
ARCHIVE_COLUMNS = (
    "aggregate_trade_id",
    "price",
    "quantity",
    "first_trade_id",
    "last_trade_id",
    "timestamp",
    "buyer_is_maker",
)
METHOD_PROVENANCE = (
    "EXTERNAL_METHOD:BINANCE_VISION_USD_M_DAILY_AGGTRADES_WITH_SHA256_CHECKSUM",
    "RESEARCH_METHOD:FLOW_ENTRY_SEQUENCE_FEATURES_USE_ONLY_TRADES_AT_OR_BEFORE_TRIGGER_CLOSE",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, destination: Path) -> None:
    if destination.exists() and destination.stat().st_size > 0:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = Request(url, headers={"User-Agent": "SMC-ICT-RE1-research/1.0"})
    try:
        with urlopen(request, timeout=120) as response, temporary.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
    except (HTTPError, URLError, TimeoutError) as exc:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"download failed {url}: {exc}") from exc
    temporary.replace(destination)


def archive_path(symbol: str, day: datetime, cache: Path) -> Path:
    stamp = day.strftime("%Y-%m-%d")
    name = f"{symbol}-aggTrades-{stamp}.zip"
    archive = cache / symbol / name
    checksum = archive.with_suffix(archive.suffix + ".CHECKSUM")
    url = f"{BASE}/{symbol}/{name}"
    _download(url, archive)
    _download(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = sha256_file(archive)
    if actual != expected:
        archive.unlink(missing_ok=True)
        raise RuntimeError(f"checksum mismatch for {archive}: {actual} != {expected}")
    return archive


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "t"}:
        return True
    if text in {"false", "0", "f"}:
        return False
    raise ValueError(f"invalid buyer-is-maker value {value!r}")


def load_archive(symbol: str, day: datetime, cache: Path) -> pd.DataFrame:
    archive = archive_path(symbol, day, cache)
    with zipfile.ZipFile(archive) as bundle:
        names = [name for name in bundle.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise RuntimeError(f"unexpected files in {archive}: {names}")
        with bundle.open(names[0]) as stream:
            frame = pd.read_csv(stream, header=None)
    if frame.shape[1] < len(ARCHIVE_COLUMNS):
        raise RuntimeError(f"unexpected aggTrade schema in {archive}: {frame.shape}")
    frame = frame.iloc[:, : len(ARCHIVE_COLUMNS)].copy()
    frame.columns = ARCHIVE_COLUMNS
    if not str(frame.iloc[0]["aggregate_trade_id"]).lstrip("-").isdigit():
        frame = frame.iloc[1:].copy()
    numeric = (
        "aggregate_trade_id",
        "price",
        "quantity",
        "first_trade_id",
        "last_trade_id",
        "timestamp",
    )
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    for column in ("aggregate_trade_id", "first_trade_id", "last_trade_id", "timestamp"):
        frame[column] = frame[column].astype("int64")
    frame["price"] = frame["price"].astype("float64")
    frame["quantity"] = frame["quantity"].astype("float64")
    frame["buyer_is_maker"] = frame["buyer_is_maker"].map(_boolean)
    frame["quote"] = frame["price"] * frame["quantity"]
    frame["signed_quote"] = frame["quote"].where(~frame["buyer_is_maker"], -frame["quote"])
    return frame.sort_values(("timestamp", "aggregate_trade_id")).reset_index(drop=True)


def dates_covering(start_ns: int, end_ns: int) -> list[datetime]:
    start = datetime.fromtimestamp(start_ns / 1e9, tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = datetime.fromtimestamp(end_ns / 1e9, tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    output: list[datetime] = []
    current = start
    while current <= end:
        output.append(current)
        current += timedelta(days=1)
    return output


def load_window(
    symbol: str,
    start_ns: int,
    end_ns: int,
    cache: Path,
    loaded: dict[tuple[str, str], pd.DataFrame],
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for day in dates_covering(start_ns, end_ns):
        key = symbol, day.strftime("%Y-%m-%d")
        if key not in loaded:
            loaded[key] = load_archive(symbol, day, cache)
        frames.append(loaded[key])
    frame = pd.concat(frames, ignore_index=True)
    start_ms = start_ns // 1_000_000
    end_ms = end_ns // 1_000_000
    return frame[(frame["timestamp"] > start_ms) & (frame["timestamp"] <= end_ms)].copy()


def _max_run(values: Iterable[tuple[bool, float]]) -> tuple[float, int]:
    best_quote = 0.0
    best_count = 0
    current_quote = 0.0
    current_count = 0
    for wanted, quote in values:
        if wanted:
            current_quote += quote
            current_count += 1
            if (current_quote, current_count) > (best_quote, best_count):
                best_quote, best_count = current_quote, current_count
        else:
            current_quote = 0.0
            current_count = 0
    return best_quote, best_count


def _continuity(frame: pd.DataFrame) -> tuple[int, int, int]:
    if len(frame) < 2:
        return 0, 0, 0
    agg = frame["aggregate_trade_id"].to_numpy(dtype="int64")
    first_ids = frame["first_trade_id"].to_numpy(dtype="int64")
    last_ids = frame["last_trade_id"].to_numpy(dtype="int64")
    agg_gaps = int((agg[1:] != agg[:-1] + 1).sum())
    raw_gaps = int((first_ids[1:] > last_ids[:-1] + 1).sum())
    overlaps = int((first_ids[1:] <= last_ids[:-1]).sum())
    return agg_gaps, raw_gaps, overlaps


@dataclass(frozen=True, slots=True)
class SequenceStudy:
    plan_id: str
    symbol: str
    family: str
    side: str
    trigger_zone_kind: str
    interaction_time_ns: int
    trigger_time_ns: int
    start_time_ns: int
    end_time_ns: int
    boundary_adverse: float
    boundary_reclaim: float
    archive_rows: int
    aggregate_id_gaps: int
    raw_trade_id_gaps: int
    raw_trade_id_overlaps: int
    first_timestamp_ms: int | None
    last_timestamp_ms: int | None
    first_penetration_ms: int | None
    reclaim_ms: int | None
    penetration_to_reclaim_ms: int | None
    penetration_price: float | None
    penetration_bps: float
    adverse_quote_outside: float
    intended_quote_outside: float
    net_signed_quote_outside: float
    quote_per_penetration_bp: float
    pre_reclaim_adverse_quote: float
    pre_reclaim_intended_quote: float
    pre_reclaim_signed_quote: float
    post_reclaim_adverse_quote: float
    post_reclaim_intended_quote: float
    post_reclaim_signed_quote: float
    max_adverse_run_quote: float
    max_adverse_run_count: int
    max_intended_run_quote: float
    max_intended_run_count: int
    price_at_first_penetration: float | None
    price_at_reclaim: float | None
    price_at_trigger_close: float | None
    post_reclaim_progress_bps: float
    sequence_has_penetration: bool
    sequence_has_reclaim: bool
    sequence_has_post_reclaim_intended_flow: bool
    sequence_has_post_reclaim_price_progress: bool
    actual_net_r: float | None
    provenance: tuple[str, ...]


def study_plan(
    record: dict[str, Any],
    frame: pd.DataFrame,
    actual_net_r: float | None,
    start_ns: int,
    end_ns: int,
) -> SequenceStudy:
    plan = record["plan"]
    side = str(plan["side"])
    long = side == "LONG"
    adverse_boundary = float(plan["overlap_lower"] if long else plan["overlap_upper"])
    reclaim_boundary = float(plan["overlap_upper"] if long else plan["overlap_lower"])
    adverse_mask = frame["buyer_is_maker"] if long else ~frame["buyer_is_maker"]
    intended_mask = ~adverse_mask
    outside_mask = frame["price"] < adverse_boundary if long else frame["price"] > adverse_boundary
    penetration_rows = frame[outside_mask]
    first_penetration = None if penetration_rows.empty else penetration_rows.iloc[0]
    first_pen_ms = None if first_penetration is None else int(first_penetration["timestamp"])

    reclaim_rows = frame[
        (frame["timestamp"] >= (first_pen_ms or 2**63 - 1))
        & (frame["price"] >= reclaim_boundary if long else frame["price"] <= reclaim_boundary)
    ]
    reclaim = None if reclaim_rows.empty else reclaim_rows.iloc[0]
    reclaim_ms = None if reclaim is None else int(reclaim["timestamp"])

    if penetration_rows.empty:
        penetration_price = None
        penetration_bps = 0.0
    elif long:
        penetration_price = float(penetration_rows["price"].min())
        penetration_bps = max(0.0, (adverse_boundary - penetration_price) / adverse_boundary * 10_000.0)
    else:
        penetration_price = float(penetration_rows["price"].max())
        penetration_bps = max(0.0, (penetration_price - adverse_boundary) / adverse_boundary * 10_000.0)

    outside_adverse = frame[outside_mask & adverse_mask]
    outside_intended = frame[outside_mask & intended_mask]
    adverse_quote_outside = float(outside_adverse["quote"].sum())
    intended_quote_outside = float(outside_intended["quote"].sum())
    net_signed_quote_outside = float(outside_intended["quote"].sum() - outside_adverse["quote"].sum())

    if reclaim_ms is None:
        pre = frame
        post = frame.iloc[0:0]
    else:
        pre = frame[frame["timestamp"] < reclaim_ms]
        post = frame[frame["timestamp"] >= reclaim_ms]

    pre_adverse = float(pre.loc[adverse_mask.reindex(pre.index, fill_value=False), "quote"].sum())
    pre_intended = float(pre.loc[intended_mask.reindex(pre.index, fill_value=False), "quote"].sum())
    post_adverse = float(post.loc[adverse_mask.reindex(post.index, fill_value=False), "quote"].sum())
    post_intended = float(post.loc[intended_mask.reindex(post.index, fill_value=False), "quote"].sum())

    adverse_run_quote, adverse_run_count = _max_run(
        zip(adverse_mask.tolist(), frame["quote"].astype(float).tolist())
    )
    intended_run_quote, intended_run_count = _max_run(
        zip(intended_mask.tolist(), frame["quote"].astype(float).tolist())
    )

    trigger_close = None if frame.empty else float(frame.iloc[-1]["price"])
    reclaim_price = None if reclaim is None else float(reclaim["price"])
    if reclaim_price is None or trigger_close is None:
        post_progress_bps = 0.0
    else:
        signed = trigger_close - reclaim_price if long else reclaim_price - trigger_close
        post_progress_bps = signed / reclaim_boundary * 10_000.0

    agg_gaps, raw_gaps, overlaps = _continuity(frame)
    return SequenceStudy(
        plan_id=str(plan["plan_id"]),
        symbol=str(plan["symbol"]),
        family=str(plan["family"]),
        side=side,
        trigger_zone_kind=str(plan.get("trigger_zone_kind", "")),
        interaction_time_ns=int(plan["interaction_time_ns"]),
        trigger_time_ns=int(plan["trigger_time_ns"]),
        start_time_ns=start_ns,
        end_time_ns=end_ns,
        boundary_adverse=adverse_boundary,
        boundary_reclaim=reclaim_boundary,
        archive_rows=len(frame),
        aggregate_id_gaps=agg_gaps,
        raw_trade_id_gaps=raw_gaps,
        raw_trade_id_overlaps=overlaps,
        first_timestamp_ms=None if frame.empty else int(frame.iloc[0]["timestamp"]),
        last_timestamp_ms=None if frame.empty else int(frame.iloc[-1]["timestamp"]),
        first_penetration_ms=first_pen_ms,
        reclaim_ms=reclaim_ms,
        penetration_to_reclaim_ms=(
            None if first_pen_ms is None or reclaim_ms is None else reclaim_ms - first_pen_ms
        ),
        penetration_price=penetration_price,
        penetration_bps=penetration_bps,
        adverse_quote_outside=adverse_quote_outside,
        intended_quote_outside=intended_quote_outside,
        net_signed_quote_outside=net_signed_quote_outside,
        quote_per_penetration_bp=(
            adverse_quote_outside / penetration_bps if penetration_bps > 0.0 else 0.0
        ),
        pre_reclaim_adverse_quote=pre_adverse,
        pre_reclaim_intended_quote=pre_intended,
        pre_reclaim_signed_quote=pre_intended - pre_adverse,
        post_reclaim_adverse_quote=post_adverse,
        post_reclaim_intended_quote=post_intended,
        post_reclaim_signed_quote=post_intended - post_adverse,
        max_adverse_run_quote=adverse_run_quote,
        max_adverse_run_count=adverse_run_count,
        max_intended_run_quote=intended_run_quote,
        max_intended_run_count=intended_run_count,
        price_at_first_penetration=(
            None if first_penetration is None else float(first_penetration["price"])
        ),
        price_at_reclaim=reclaim_price,
        price_at_trigger_close=trigger_close,
        post_reclaim_progress_bps=post_progress_bps,
        sequence_has_penetration=first_pen_ms is not None,
        sequence_has_reclaim=reclaim_ms is not None,
        sequence_has_post_reclaim_intended_flow=post_intended > post_adverse,
        sequence_has_post_reclaim_price_progress=post_progress_bps > 0.0,
        actual_net_r=actual_net_r,
        provenance=METHOD_PROVENANCE,
    )


def read_windows(path: Path) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                output.append(json.loads(line))
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pre-seconds", type=int, default=60)
    parser.add_argument("--post-seconds", type=int, default=0)
    parser.add_argument("--all-plans", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    windows = read_windows(args.artifact_dir / "mtf_trade_windows.jsonl")
    audit_path = args.artifact_dir / "trade_audit.csv"
    audit = pd.read_csv(audit_path) if audit_path.exists() else pd.DataFrame()
    pnl = (
        audit.set_index("plan_id")["actual_net_r"].to_dict()
        if not audit.empty and "actual_net_r" in audit.columns
        else {}
    )

    selected = [
        item
        for item in windows
        if args.all_plans or str(item["plan"].get("trigger_zone_kind", "")).startswith("FLOW_")
    ]
    loaded: dict[tuple[str, str], pd.DataFrame] = {}
    studies: list[SequenceStudy] = []
    errors: list[dict[str, Any]] = []
    for item in selected:
        plan = item["plan"]
        trigger_ns = int(plan["trigger_time_ns"])
        start_ns = trigger_ns - args.pre_seconds * 1_000_000_000
        end_ns = trigger_ns + args.post_seconds * 1_000_000_000
        try:
            frame = load_window(str(plan["symbol"]), start_ns, end_ns, args.cache, loaded)
            studies.append(
                study_plan(
                    item,
                    frame,
                    None if plan["plan_id"] not in pnl else float(pnl[plan["plan_id"]]),
                    start_ns,
                    end_ns,
                )
            )
        except Exception as exc:  # preserve event-specific evidence and continue
            errors.append(
                {
                    "plan_id": plan.get("plan_id"),
                    "symbol": plan.get("symbol"),
                    "trigger_time_ns": trigger_ns,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    records = [asdict(item) for item in studies]
    frame = pd.DataFrame(records)
    frame.to_csv(args.output / "aggtrade_absorption_event_study.csv", index=False)
    with (args.output / "aggtrade_absorption_event_study.jsonl").open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    summary = {
        "selected_plans": len(selected),
        "completed_studies": len(studies),
        "errors": errors,
        "archives_loaded": [f"{symbol}:{day}" for symbol, day in sorted(loaded)],
        "feature_horizon": {
            "pre_seconds": args.pre_seconds,
            "post_seconds": args.post_seconds,
            "future_information_used": False,
        },
        "provenance": METHOD_PROVENANCE,
    }
    (args.output / "aggtrade_absorption_event_study_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if errors:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    if selected and not studies:
        raise RuntimeError("no aggTrade event studies completed")


if __name__ == "__main__":
    main()
