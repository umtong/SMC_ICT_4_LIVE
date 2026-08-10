#!/usr/bin/env python3
"""Attach Binance Vision futures metrics to audited 4h jump boundaries.

This is an external-state forensic, not a trading rule and not a backtest.  It
reuses Binance's public daily ``metrics`` archives to ask whether the losing
all-symbol continuation boundary and profitable reversal boundaries differed
in observable leverage/positioning state before the decision.

Only rows timestamped at or before the completed 4h boundary are used as causal
features.  Post-boundary metrics are retained in a separately labelled field
for mechanism diagnosis and can never be used as entry-time input on this
interval.
"""
from __future__ import annotations

from collections import defaultdict
import csv
from datetime import date, datetime, timedelta, timezone
from io import BytesIO, StringIO, TextIOWrapper
import json
import math
from pathlib import Path
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

import pandas as pd

HERE = Path(__file__).resolve().parent
AUDIT = HERE / "evidence" / "jump-all-candidate-forensic-v1" / "episode_rows.json"
OUT = HERE / "evidence" / "jump-binance-metrics-forensic-v1"
CACHE = Path(".cache/candidate-57-jump-binance-metrics-forensic-v1")
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
START = date(2025, 12, 1)
END = date(2025, 12, 14)
BASE = "https://data.binance.vision/data/futures/um/daily/metrics"
_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=str)
        + "\n",
        encoding="utf-8",
    )


def normal(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def numeric(value: Any) -> float | None:
    if value is None:
        return None
    match = _NUMBER.search(str(value).replace(",", "").replace("_", ""))
    if match is None:
        return None
    try:
        result = float(match.group(0))
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def parse_timestamp(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    raw = numeric(text)
    if raw is not None and re.fullmatch(r"[-+]?\d+(?:\.0+)?", text):
        magnitude = abs(raw)
        if magnitude > 1e17:
            return int(raw)
        if magnitude > 1e14:
            return int(raw * 1_000)
        if magnitude > 1e11:
            return int(raw * 1_000_000)
        if magnitude > 1e9:
            return int(raw * 1_000_000_000)
    try:
        stamp = pd.Timestamp(text)
    except Exception:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return int(stamp.value)


def date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def download(symbol: str, day: date) -> Path | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    filename = f"{symbol}-metrics-{day.isoformat()}.zip"
    path = CACHE / filename
    if path.is_file() and path.stat().st_size > 0:
        return path
    url = f"{BASE}/{symbol}/{filename}"
    request = Request(url, headers={"User-Agent": "candidate-57-research/1.0"})
    for attempt in range(4):
        try:
            with urlopen(request, timeout=45) as response:
                payload = response.read()
            if not payload:
                raise RuntimeError(f"empty payload: {url}")
            path.write_bytes(payload)
            return path
        except HTTPError as exc:
            if exc.code == 404:
                return None
            if attempt == 3:
                raise
        except (URLError, TimeoutError, RuntimeError):
            if attempt == 3:
                raise
        time.sleep(1.5 * (attempt + 1))
    return None


def read_archive(path: Path, symbol: str, day: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if not names:
            return rows
        with archive.open(names[0]) as raw:
            text = TextIOWrapper(raw, encoding="utf-8-sig", newline="")
            reader = csv.DictReader(text)
            for record in reader:
                normalized = {normal(key): value for key, value in record.items()}
                timestamp_value = next(
                    (
                        normalized[key]
                        for key in (
                            "create_time",
                            "timestamp",
                            "time",
                            "ts",
                        )
                        if key in normalized
                    ),
                    None,
                )
                ts = parse_timestamp(timestamp_value)
                if ts is None:
                    continue
                numeric_fields = {
                    key: value
                    for key, raw_value in normalized.items()
                    if key not in {"create_time", "timestamp", "time", "ts", "symbol"}
                    and (value := numeric(raw_value)) is not None
                }
                rows.append(
                    {
                        "ts_event": ts,
                        "timestamp_utc": pd.Timestamp(ts, unit="ns", tz="UTC").isoformat(),
                        "symbol": symbol,
                        "source_day": day.isoformat(),
                        "fields": numeric_fields,
                    }
                )
    rows.sort(key=lambda row: int(row["ts_event"]))
    return rows


def load_metrics() -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    result: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in SYMBOLS}
    manifest: dict[str, Any] = {"downloaded": [], "missing": [], "schemas": {}}
    for symbol in SYMBOLS:
        for day in date_range(START, END):
            path = download(symbol, day)
            if path is None:
                manifest["missing"].append(
                    {"symbol": symbol, "day": day.isoformat()}
                )
                continue
            rows = read_archive(path, symbol, day)
            result[symbol].extend(rows)
            manifest["downloaded"].append(
                {
                    "symbol": symbol,
                    "day": day.isoformat(),
                    "file": path.name,
                    "bytes": path.stat().st_size,
                    "rows": len(rows),
                }
            )
        result[symbol].sort(key=lambda row: int(row["ts_event"]))
        field_names = sorted(
            {
                key
                for row in result[symbol]
                for key in (row.get("fields") or {}).keys()
            }
        )
        manifest["schemas"][symbol] = {
            "rows": len(result[symbol]),
            "first_ts": result[symbol][0]["timestamp_utc"] if result[symbol] else None,
            "last_ts": result[symbol][-1]["timestamp_utc"] if result[symbol] else None,
            "fields": field_names,
        }
    return result, manifest


def nearest_at_or_before(
    rows: list[dict[str, Any]], ts: int, max_age_minutes: int = 15
) -> dict[str, Any] | None:
    lo = 0
    hi = len(rows)
    while lo < hi:
        mid = (lo + hi) // 2
        if int(rows[mid]["ts_event"]) <= ts:
            lo = mid + 1
        else:
            hi = mid
    index = lo - 1
    if index < 0:
        return None
    row = rows[index]
    age_minutes = (ts - int(row["ts_event"])) / 60_000_000_000.0
    if age_minutes < -1e-9 or age_minutes > max_age_minutes:
        return None
    return {**row, "age_minutes_at_decision": age_minutes}


def nearest_at_or_after(
    rows: list[dict[str, Any]], ts: int, max_delay_minutes: int = 15
) -> dict[str, Any] | None:
    lo = 0
    hi = len(rows)
    while lo < hi:
        mid = (lo + hi) // 2
        if int(rows[mid]["ts_event"]) < ts:
            lo = mid + 1
        else:
            hi = mid
    if lo >= len(rows):
        return None
    row = rows[lo]
    delay_minutes = (int(row["ts_event"]) - ts) / 60_000_000_000.0
    if delay_minutes < -1e-9 or delay_minutes > max_delay_minutes:
        return None
    return {**row, "delay_minutes_after_decision": delay_minutes}


def row_at_offset(
    rows: list[dict[str, Any]], ts: int, offset_minutes: int
) -> dict[str, Any] | None:
    target = ts + offset_minutes * 60_000_000_000
    return (
        nearest_at_or_before(rows, target, max_age_minutes=10)
        if offset_minutes <= 0
        else nearest_at_or_after(rows, target, max_delay_minutes=10)
    )


def change(current: dict[str, Any] | None, prior: dict[str, Any] | None) -> dict[str, Any]:
    if current is None or prior is None:
        return {}
    current_fields = current.get("fields") or {}
    prior_fields = prior.get("fields") or {}
    output: dict[str, Any] = {}
    for key in sorted(set(current_fields) & set(prior_fields)):
        now = float(current_fields[key])
        before = float(prior_fields[key])
        output[key] = {
            "absolute": now - before,
            "fraction": (now / before - 1.0) if abs(before) > 1e-12 else None,
        }
    return output


def group_audit(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["episode_ts"])].append(row)
    boundaries = []
    for ts, candidates in sorted(grouped.items()):
        candidates.sort(key=lambda row: str(row["symbol"]))
        resolved = [
            float(row["exit_net_r"])
            for row in candidates
            if row.get("exit_net_r") is not None
        ]
        selected = next(
            (row for row in candidates if bool(row.get("router_selected"))),
            None,
        )
        boundaries.append(
            {
                "episode_ts": ts,
                "timestamp_utc": pd.Timestamp(ts, unit="ns", tz="UTC").isoformat(),
                "candidate_count": len(candidates),
                "symbols": [row["symbol"] for row in candidates],
                "sides": sorted({int(row["side"]) for row in candidates}),
                "all_positive": bool(resolved) and all(value > 0.0 for value in resolved),
                "all_negative": bool(resolved) and all(value < 0.0 for value in resolved),
                "positive_count": sum(value > 0.0 for value in resolved),
                "negative_count": sum(value < 0.0 for value in resolved),
                "mean_shadow_r": sum(resolved) / len(resolved) if resolved else None,
                "selected_symbol": None if selected is None else selected["symbol"],
                "selected_actual_r": (
                    None if selected is None else selected.get("actual_after_cost_r")
                ),
                "candidate_outcomes": [
                    {
                        "symbol": row["symbol"],
                        "side": row["side"],
                        "causal_zscore": (row.get("diagnostics") or {}).get(
                            "causal_zscore"
                        ),
                        "absolute_return": (row.get("diagnostics") or {}).get(
                            "absolute_return"
                        ),
                        "exit_net_r": row.get("exit_net_r"),
                        "outcome": row.get("outcome"),
                        "router_selected": row.get("router_selected"),
                        "actual_executed": row.get("actual_executed"),
                    }
                    for row in candidates
                ],
            }
        )
    return boundaries


def attach_metrics(
    boundaries: list[dict[str, Any]],
    metrics: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    enriched = []
    for boundary in boundaries:
        ts = int(boundary["episode_ts"])
        snapshots: dict[str, Any] = {}
        for symbol in SYMBOLS:
            rows = metrics[symbol]
            current = nearest_at_or_before(rows, ts)
            prior_15 = row_at_offset(rows, ts, -15)
            prior_60 = row_at_offset(rows, ts, -60)
            after_15 = row_at_offset(rows, ts, 15)
            after_60 = row_at_offset(rows, ts, 60)
            snapshots[symbol] = {
                "causal_at_or_before": current,
                "causal_change_15m": change(current, prior_15),
                "causal_change_60m": change(current, prior_60),
                "post_outcome_only_after_15m": after_15,
                "post_outcome_only_change_15m": change(after_15, current),
                "post_outcome_only_after_60m": after_60,
                "post_outcome_only_change_60m": change(after_60, current),
            }
        enriched.append({**boundary, "metrics_by_symbol": snapshots})
    return enriched


def field_candidates(metrics: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    return {
        symbol: sorted(
            {
                key
                for row in rows
                for key in (row.get("fields") or {}).keys()
            }
        )
        for symbol, rows in metrics.items()
    }


def compact_boundary_table(boundaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table = []
    preferred = (
        "sum_open_interest",
        "sum_open_interest_value",
        "count_toptrader_long_short_ratio",
        "sum_toptrader_long_short_ratio",
        "count_long_short_ratio",
        "sum_taker_long_short_vol_ratio",
    )
    for boundary in boundaries:
        record = {
            key: boundary[key]
            for key in (
                "episode_ts",
                "timestamp_utc",
                "candidate_count",
                "symbols",
                "sides",
                "all_positive",
                "all_negative",
                "positive_count",
                "negative_count",
                "mean_shadow_r",
                "selected_symbol",
                "selected_actual_r",
            )
        }
        record["causal_metrics"] = {}
        for symbol, snapshot in boundary["metrics_by_symbol"].items():
            current = snapshot.get("causal_at_or_before") or {}
            fields = current.get("fields") or {}
            selected_fields = {
                key: fields[key]
                for key in preferred
                if key in fields
            }
            # Preserve any schema variants that clearly refer to OI, long/short
            # or taker positioning without assuming an exact column spelling.
            for key, value in fields.items():
                if any(token in key for token in ("open_interest", "long_short", "taker")):
                    selected_fields.setdefault(key, value)
            record["causal_metrics"][symbol] = {
                "timestamp_utc": current.get("timestamp_utc"),
                "age_minutes": current.get("age_minutes_at_decision"),
                "fields": selected_fields,
                "change_15m": snapshot.get("causal_change_15m"),
                "change_60m": snapshot.get("causal_change_60m"),
            }
        table.append(record)
    return table


def main() -> int:
    if not AUDIT.is_file():
        raise RuntimeError(f"jump audit evidence missing: {AUDIT}")
    audit_rows = json.loads(AUDIT.read_text(encoding="utf-8"))
    metrics, manifest = load_metrics()
    boundaries = group_audit(audit_rows)
    enriched = attach_metrics(boundaries, metrics)
    compact = compact_boundary_table(enriched)

    if OUT.exists():
        for child in OUT.iterdir():
            if child.is_file():
                child.unlink()
    OUT.mkdir(parents=True, exist_ok=True)
    dump(OUT / "download_manifest.json", manifest)
    dump(OUT / "available_fields.json", field_candidates(metrics))
    dump(OUT / "boundary_metrics_full.json", enriched)
    dump(OUT / "boundary_metrics_compact.json", compact)

    focus = [
        row
        for row in compact
        if str(row["timestamp_utc"]).startswith("2025-12-07")
        or str(row["timestamp_utc"]).startswith("2025-12-09")
    ]
    dump(OUT / "focus_dec07_vs_dec09.json", focus)
    summary = {
        "source": "Binance Vision futures/um daily metrics archives",
        "causal_contract": (
            "entry-time fields use only the latest metrics row at or before "
            "the completed 4h boundary; post-boundary rows are separately labelled"
        ),
        "symbols": list(SYMBOLS),
        "dates": [START.isoformat(), END.isoformat()],
        "audited_boundaries": len(boundaries),
        "downloaded_archives": len(manifest["downloaded"]),
        "missing_archives": len(manifest["missing"]),
        "schemas": manifest["schemas"],
        "all_negative_boundaries": [
            row["timestamp_utc"] for row in compact if row["all_negative"]
        ],
        "all_positive_boundaries": [
            row["timestamp_utc"] for row in compact if row["all_positive"]
        ],
        "interpretation_limit": (
            "This development diagnostic may motivate a pre-frozen external "
            "state rule; it cannot validate a rule selected from these outcomes."
        ),
    }
    dump(OUT / "summary.json", summary)
    (OUT / "README.md").write_text(
        "# Jump Binance metrics forensic\n\n"
        "This is an external-state causal audit, not a trading result.  Use "
        "`boundary_metrics_compact.json` to compare leverage/positioning at "
        "the audited 4h boundaries.  Fields after the boundary are explicitly "
        "post-outcome-only and cannot be reused as entry-time inputs in this "
        "development interval.\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False))
    return 0 if manifest["downloaded"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
