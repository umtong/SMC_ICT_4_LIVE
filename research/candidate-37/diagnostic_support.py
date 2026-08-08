"""Input validation and post-decision labels for Candidate 37 diagnostics."""
from __future__ import annotations

from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import median
from typing import Any

import pandas as pd

from router import BarObservation, RouteDecision, SYMBOLS


class DiagnosticError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def percentiles(values: list[float]) -> dict[str, float | None]:
    clean = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not clean:
        return {"min": None, "p25": None, "median": None, "p75": None, "max": None}

    def pick(fraction: float) -> float:
        position = fraction * (len(clean) - 1)
        low, high = int(math.floor(position)), int(math.ceil(position))
        if low == high:
            return clean[low]
        weight = position - low
        return clean[low] * (1.0 - weight) + clean[high] * weight

    return {
        "min": clean[0], "p25": pick(0.25), "median": float(median(clean)),
        "p75": pick(0.75), "max": clean[-1],
    }


def manifest_map(input_root: Path) -> dict[str, tuple[dict[str, Any], Path]]:
    result: dict[str, tuple[dict[str, Any], Path]] = {}
    for path in sorted(input_root.rglob("chunk_manifest.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        symbol = str(manifest.get("symbol", ""))
        if symbol not in SYMBOLS:
            continue
        if symbol in result:
            raise DiagnosticError(f"multiple input chunks for {symbol}")
        directory = path.parent
        for filename in ("klines.csv.gz", "features.csv.gz"):
            candidate = directory / filename
            expected = manifest.get("files", {}).get(filename, {}).get("sha256")
            if not candidate.is_file() or not expected:
                raise DiagnosticError(f"{symbol} manifest does not own {filename}")
            if sha256_file(candidate) != expected:
                raise DiagnosticError(f"{symbol} {filename} sha256 mismatch")
        result[symbol] = (manifest, directory)
    missing = [symbol for symbol in SYMBOLS if symbol not in result]
    if missing:
        raise DiagnosticError(f"missing chunks: {missing}")
    return result


def load_symbol(
    *, symbol: str, manifest: dict[str, Any], directory: Path
) -> tuple[list[BarObservation], list[int]]:
    frame = pd.read_csv(directory / "klines.csv.gz", compression="gzip")
    required = {"close_time_dt", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        raise DiagnosticError(f"{symbol} missing columns {sorted(required - set(frame.columns))}")
    if len(frame) != int(manifest["rows"]):
        raise DiagnosticError(f"{symbol} row count differs from manifest")
    times = [
        int(pd.Timestamp(value).value)
        for value in pd.to_datetime(frame["close_time_dt"], utc=True, errors="raise")
    ]
    if any(current <= previous for previous, current in zip(times, times[1:])):
        raise DiagnosticError(f"{symbol} timestamps are not strictly monotonic")
    bars = [
        BarObservation(
            ts_event=timestamp, open=float(row.open), high=float(row.high),
            low=float(row.low), close=float(row.close), volume=float(row.volume),
        )
        for timestamp, row in zip(
            times,
            frame[["open", "high", "low", "close", "volume"]].itertuples(index=False),
        )
    ]
    return bars, times


def geometry(decision: RouteDecision) -> tuple[bool, float, float]:
    if not decision.actionable:
        return True, math.nan, math.nan
    risk = decision.side * (decision.entry_reference - decision.stop_reference)
    reward = decision.side * (decision.objective_reference - decision.entry_reference)
    valid = math.isfinite(risk) and math.isfinite(reward) and risk > 0 and reward > 0
    return valid, reward / risk if valid else math.nan, risk


def forward_label(
    *, bars: list[BarObservation], index: int, decision: RouteDecision, horizon: int
) -> dict[str, Any]:
    valid, rr, risk = geometry(decision)
    if not valid:
        return {"outcome": "INVALID_GEOMETRY"}
    side, target, stop = decision.side, decision.objective_reference, decision.stop_reference
    first: str | None = None
    first_offset: int | None = None
    favorable, adverse = 0.0, 0.0
    markouts: dict[str, float | None] = {}
    future = bars[index + 1 : min(len(bars), index + horizon + 1)]
    for offset, bar in enumerate(future, start=1):
        if side > 0:
            target_hit, stop_hit = bar.high >= target, bar.low <= stop
            favorable = max(favorable, (bar.high - decision.entry_reference) / risk)
            adverse = min(adverse, (bar.low - decision.entry_reference) / risk)
        else:
            target_hit, stop_hit = bar.low <= target, bar.high >= stop
            favorable = max(favorable, (decision.entry_reference - bar.low) / risk)
            adverse = min(adverse, (decision.entry_reference - bar.high) / risk)
        if first is None and target_hit and stop_hit:
            first, first_offset = "AMBIGUOUS_SAME_BAR", offset
        elif first is None and target_hit:
            first, first_offset = "TARGET_FIRST", offset
        elif first is None and stop_hit:
            first, first_offset = "STOP_FIRST", offset
        if offset in (5, 15, 30, 60, 90):
            markouts[str(offset)] = side * (bar.close - decision.entry_reference) / risk
    for offset in (5, 15, 30, 60, 90):
        markouts.setdefault(str(offset), None)
    return {
        "outcome": first or "NEITHER_WITHIN_HORIZON",
        "first_hit_offset_minutes": first_offset,
        "geometry_rr": rr, "max_favorable_r": favorable,
        "max_adverse_r": adverse, "markout_r": markouts,
    }
