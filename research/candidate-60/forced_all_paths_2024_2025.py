#!/usr/bin/env python3
"""Materialize every frozen Candidate-16 liquidation-event path for 2024-2025.

This is development-data reconstruction after the pre-registered C60 forced
rejection rule failed on the same interval.  It creates no strategy, PnL, NAV,
or promotion claim.  Its only purpose is to recover the complete causal state
space (including events rejected by V1) so the next market model can distinguish
flow continuation, absorption, release, and unresolved states without tuning on
only the selected V1 trades.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

import pandas as pd

import forced_rejection_holdout_v1 as holdout
import v10_liquidation_path_diagnostic as path_source
import v9_tardis_liquidation_study as base

SOURCE_COMMIT = "d35fe7c3556a387933103e18d491ab56d2f37c18"
SCHEMA = "candidate-60-forced-all-paths-2024-2025-v1"


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def run(
    *,
    start_month: str,
    end_month: str,
    cache: Path,
    output: Path,
) -> dict[str, Any]:
    days, obtained = holdout._obtain_all(base, start_month, end_month, cache)
    panel = holdout._build_panel(base, days, obtained)
    events = base.classify_and_score(panel)
    if events.empty:
        raise RuntimeError("frozen event contract produced no independent episodes")
    events = events.sort_values(["minute", "symbol"], kind="stable").reset_index(drop=True)
    paths = path_source.extract_paths(panel, events)
    if paths.empty:
        raise RuntimeError("no complete event-time paths were extracted")

    events_with_id = events.copy()
    events_with_id.insert(0, "event_id", range(len(events_with_id)))
    output.mkdir(parents=True, exist_ok=True)
    events_path = output / "events.csv"
    paths_path = output / "paths.csv.gz"
    events_with_id.to_csv(events_path, index=False)
    paths.to_csv(paths_path, index=False, compression="gzip")

    result = {
        "schema": SCHEMA,
        "role": (
            "consumed-interval development state reconstruction; no strategy, "
            "fills, account, NAV, or promotion claim"
        ),
        "source_commit": SOURCE_COMMIT,
        "sample_contract": {
            "start_month": start_month,
            "end_month": end_month,
            "first_calendar_day_of_each_month": True,
            "calendar_days": int(len(days)),
            "symbol_days": int(len(days) * len(base.SYMBOLS)),
        },
        "global_independent_events": int(len(events_with_id)),
        "path_rows": int(len(paths)),
        "event_regime_counts": {
            str(key): int(value)
            for key, value in events_with_id["regime"].value_counts().sort_index().items()
        },
        "path_regime_counts": {
            str(key): int(value)
            for key, value in paths.groupby("event_regime")["event_id"].nunique().sort_index().items()
        },
        "causal_contract": {
            "event_definition_reused_unchanged": True,
            "path_features_at_each_t_use_only_completed_observations_through_t": True,
            "future_path_rows_are_labels_only": True,
            "thresholds_searched": 0,
        },
        "source_hashes": {
            "v9_tardis_liquidation_study.py": _sha256(Path(base.__file__)),
            "v10_liquidation_path_diagnostic.py": _sha256(Path(path_source.__file__)),
            "forced_rejection_holdout_v1.py": _sha256(Path(holdout.__file__)),
        },
        "output_hashes": {
            "events.csv": _sha256(events_path),
            "paths.csv.gz": _sha256(paths_path),
        },
    }
    _write_json(output / "summary.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-month", default="2024-01")
    parser.add_argument("--end-month", default="2025-12")
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(
        start_month=args.start_month,
        end_month=args.end_month,
        cache=args.cache.resolve(),
        output=args.output.resolve(),
    )
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
