#!/usr/bin/env python3
"""Post-holdout causal path forensics for Candidate 60 forced rejection V1.

The 2024-2025 untouched result has already been consumed and failed.  Those
observations are development data from this point forward.  This diagnostic
reuses the immutable Candidate 16 event contract and the unchanged V1
transition, then persists only paths belonging to selected transitions so the
failure can be separated into event-state, transition, target-space, and
management errors.  It makes no strategy, fill, account, NAV, or promotion
claim.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

import pandas as pd

import forced_rejection_holdout_v1 as frozen


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
    import v9_tardis_liquidation_study as base
    import v9_tardis_liquidation_study_v3 as compatibility  # noqa: F401
    import v10_liquidation_path_diagnostic as path_source

    days, obtained = frozen._obtain_all(base, start_month, end_month, cache)
    panel = frozen._build_panel(base, days, obtained)
    events = base.classify_and_score(panel)
    if events.empty:
        raise base.StudyError("frozen event contract produced no events")
    events = events.sort_values(["minute", "symbol"], kind="stable").reset_index(drop=True)
    paths = path_source.extract_paths(panel, events)
    transitions = frozen.select_transitions(paths)

    selected_ids = set(int(value) for value in transitions["event_id"].tolist())
    transition_paths = paths[paths["event_id"].isin(selected_ids)].copy()
    transition_paths = transition_paths.sort_values(
        ["event_id", "t_min"], kind="stable"
    ).reset_index(drop=True)

    output.mkdir(parents=True, exist_ok=True)
    events.to_csv(output / "events.csv", index=False)
    transitions.to_csv(output / "transitions.csv", index=False)
    transition_paths.to_csv(
        output / "transition_paths.csv.gz",
        index=False,
        compression={"method": "gzip", "compresslevel": 9, "mtime": 0},
    )

    result = {
        "schema": "candidate-60-forced-rejection-forensics-v2",
        "role": (
            "post-failed-holdout development path diagnostic; no strategy, "
            "fills, account, NAV, or promotion claim"
        ),
        "source_commit": frozen.SOURCE_COMMIT,
        "v1_rule_id_reused_unchanged": frozen.RULE_ID,
        "holdout_status_before_this_diagnostic": "CONSUMED_AND_STRUCTURALLY_FAILED",
        "sample_contract": {
            "start_month": start_month,
            "end_month": end_month,
            "calendar_days": len(days),
            "symbol_days": len(days) * len(base.SYMBOLS),
            "first_calendar_day_of_each_month": True,
        },
        "global_independent_events": int(len(events)),
        "selected_transitions": int(len(transitions)),
        "selected_transition_events": int(transitions["event_id"].nunique()),
        "transition_path_rows": int(len(transition_paths)),
        "regime_counts": {
            str(key): int(value)
            for key, value in events["regime"].value_counts().sort_index().items()
        },
        "transition_regime_counts": {
            str(key): int(value)
            for key, value in transitions["event_regime"].value_counts().sort_index().items()
        },
        "source_hashes": {
            "v9_tardis_liquidation_study.py": _sha256(Path(base.__file__).resolve()),
            "v10_liquidation_path_diagnostic.py": _sha256(
                Path(path_source.__file__).resolve()
            ),
            "forced_rejection_holdout_v1.py": _sha256(
                Path(frozen.__file__).resolve()
            ),
        },
        "allowed_use": (
            "diagnose and redesign on 2021-2025 development data, then freeze a "
            "new causal rule before reading any 2026 monthly sample outcome"
        ),
    }
    _write_json(output / "forensics_summary.json", result)
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
