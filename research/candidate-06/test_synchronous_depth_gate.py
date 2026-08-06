#!/usr/bin/env python3
"""Pure causality and classification checks for the FATR depth gate."""

from __future__ import annotations

import csv
from pathlib import Path
import tempfile
from types import SimpleNamespace

from synchronous_depth_gate import evaluate_failed_acceptance_depth


SECOND = 1_000_000_000


def _write(rows: list[tuple[int, float, float]]) -> Path:
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", suffix=".csv", delete=False)
    with handle:
        writer = csv.DictWriter(handle, fieldnames=["ts_ns", "bid_near", "ask_near"])
        writer.writeheader()
        for ts_ns, bid, ask in rows:
            writer.writerow({"ts_ns": ts_ns, "bid_near": bid, "ask_near": ask})
    return Path(handle.name)


def _evaluate(path: Path, direction: str = "LONG"):
    anchor = 1_000 * SECOND
    decision = anchor + 60 * SECOND
    original = SimpleNamespace(observed_ts_ns=anchor)
    trap = SimpleNamespace(direction=direction)
    snapshot = SimpleNamespace(observation=SimpleNamespace(ts_ns=decision))
    return evaluate_failed_acceptance_depth(
        original,
        trap,
        snapshot,
        {
            "depth_series_path": str(path),
            "fatr_depth_pre_window_seconds": 120,
            "fatr_depth_max_age_seconds": 90,
            "fatr_depth_min_pre_records": 2,
            "fatr_depth_min_event_records": 2,
            "fatr_depth_final_records": 1,
            "fatr_depth_min_recovery_fraction": 0.50,
        },
    )


def main() -> int:
    anchor = 1_000 * SECOND

    long_pass = _write(
        [
            (anchor - 90 * SECOND, 100.0, 100.0),
            (anchor - 30 * SECOND, 100.0, 100.0),
            (anchor + 15 * SECOND, 60.0, 105.0),
            (anchor + 45 * SECOND, 95.0, 80.0),
            (anchor + 75 * SECOND, 10_000.0, 1.0),
        ],
    )
    result = _evaluate(long_pass, "LONG")
    assert result.passed, result
    assert result.metrics["last_observation_ts_ns"] == anchor + 45 * SECOND
    assert result.metrics["source_side"] == "BID"

    short_pass = _write(
        [
            (anchor - 90 * SECOND, 100.0, 100.0),
            (anchor - 30 * SECOND, 100.0, 100.0),
            (anchor + 10 * SECOND, 105.0, 60.0),
            (anchor + 40 * SECOND, 80.0, 95.0),
        ],
    )
    result = _evaluate(short_pass, "SHORT")
    assert result.passed, result
    assert result.metrics["source_side"] == "ASK"

    no_recovery = _write(
        [
            (anchor - 90 * SECOND, 100.0, 100.0),
            (anchor - 30 * SECOND, 100.0, 100.0),
            (anchor + 10 * SECOND, 40.0, 105.0),
            (anchor + 40 * SECOND, 50.0, 80.0),
        ],
    )
    result = _evaluate(no_recovery, "LONG")
    assert not result.passed
    assert result.reason == "TRAP_SOURCE_SIDE_NOT_REPLENISHED"

    closed_path = _write(
        [
            (anchor - 90 * SECOND, 100.0, 100.0),
            (anchor - 30 * SECOND, 100.0, 100.0),
            (anchor + 10 * SECOND, 80.0, 120.0),
            (anchor + 40 * SECOND, 100.0, 150.0),
        ],
    )
    result = _evaluate(closed_path, "LONG")
    assert not result.passed
    assert result.reason == "TRAP_TARGET_PATH_NOT_RELATIVELY_OPEN"

    insufficient = _write(
        [
            (anchor - 30 * SECOND, 100.0, 100.0),
            (anchor + 40 * SECOND, 100.0, 80.0),
        ],
    )
    result = _evaluate(insufficient, "LONG")
    assert not result.passed
    assert result.reason == "DEPTH_PRE_WINDOW_INSUFFICIENT"

    for path in (long_pass, short_pass, no_recovery, closed_path, insufficient):
        path.unlink(missing_ok=True)
    print("synchronous depth gate tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
