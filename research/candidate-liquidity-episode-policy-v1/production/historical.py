from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

from .policy_bridge import activate_restored_policy_paths
from .producer import write_json_atomic


def _month_windows(start: date, end: date) -> Iterator[tuple[date, date]]:
    cursor = pd.Timestamp(start)
    hard_end = pd.Timestamp(end)
    while cursor < hard_end:
        next_month = cursor + pd.offsets.MonthBegin(1)
        window_end = min(next_month, hard_end)
        yield cursor.date(), window_end.date()
        cursor = window_end


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_continuous_reproduction(
    *,
    start: date,
    end: date,
    development_end: date,
    symbols: tuple[str, ...],
    warmup_days: int,
    cache: Path,
    output: Path,
) -> dict[str, Any]:
    if not (start < development_end < end):
        raise ValueError("require start < development_end < end")
    activate_restored_policy_paths()
    import reproduce

    harvest_root = output / "harvest"
    route_root = output / "account"
    harvest_root.mkdir(parents=True, exist_ok=True)
    route_root.mkdir(parents=True, exist_ok=True)
    periods = []
    for window_start, window_end in _month_windows(start, end):
        role = "dev" if window_end <= development_end else "eval"
        # A month straddling the frozen development boundary is split exactly.
        if window_start < development_end < window_end:
            subwindows = [(window_start, development_end, "dev"), (development_end, window_end, "eval")]
        else:
            subwindows = [(window_start, window_end, role)]
        for sub_start, sub_end, sub_role in subwindows:
            if sub_end <= sub_start:
                continue
            period = f"{sub_role}-{sub_start.year}-m{sub_start.month:02d}d{sub_start.day:02d}"
            destination = harvest_root / period
            summary = reproduce.harvest(
                start=sub_start,
                end=sub_end,
                warmup_days=warmup_days,
                symbols=symbols,
                cache=cache,
                output=destination,
                period=period,
            )
            periods.append(
                {
                    "period": period,
                    "role": sub_role,
                    "start": sub_start.isoformat(),
                    "end": sub_end.isoformat(),
                    "summary": summary,
                }
            )
    reproduce.route(harvest_root, route_root)
    inspection_path = output / "inspection.json"
    inspection = reproduce.inspect(harvest_root, inspection_path)
    files = []
    for path in sorted(output.rglob("*")):
        if path.is_file():
            files.append(
                {
                    "path": str(path.relative_to(output)),
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    manifest = {
        "start": start.isoformat(),
        "development_end": development_end.isoformat(),
        "end": end.isoformat(),
        "symbols": list(symbols),
        "warmup_days": warmup_days,
        "periods": periods,
        "inspection": inspection,
        "account_output": str(route_root),
        "files": files,
        "continuous_account_contract": (
            "all four symbols share the restored strict chronological router and one global pending/position slot"
        ),
        "development_boundary_was_frozen_before_evaluation": True,
    }
    write_json_atomic(output / "continuous_manifest.json", manifest)
    return manifest
