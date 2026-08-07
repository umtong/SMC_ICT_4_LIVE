#!/usr/bin/env python3
"""Freeze untouched multi-session holdouts before any market-data access.

Selection reads dates and source hashes only. It deliberately never imports or
opens market data, prior trade outcomes, bars, or event files. Candidate starts
are shuffled once with a committed seed, then the first three seven-day
intervals whose warmup+evaluation footprints do not overlap any previously
opened Candidate 11 market-data footprint are selected.
"""
from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import random
import subprocess
from typing import Any, Iterator

SEED = 2026080819
RANGE_START = date(2023, 1, 1)
LAST_START = date(2025, 12, 25)
WARMUP_DAYS = 3
EVALUATION_DAYS = 7
# Existing protocols do not all encode their downloader warmup beside each
# interval. Expanding every prior interval by seven days on the left is a
# deliberately conservative superset of all known Candidate 11 warmups.
PRIOR_OPENED_DATA_BUFFER_DAYS = 7
OUTPUT_NAME = "holdout_protocol.json"
STRATEGY_RELATIVE = Path("session_portfolio_v1")


def iter_date_mappings(value: Any) -> Iterator[tuple[date, date]]:
    if isinstance(value, dict):
        start = value.get("start")
        end = value.get("end_exclusive")
        if isinstance(start, str) and isinstance(end, str):
            begin = date.fromisoformat(start)
            finish = date.fromisoformat(end)
            if finish <= begin:
                raise SystemExit(f"invalid frozen interval: {value}")
            yield begin, finish
        for child in value.values():
            yield from iter_date_mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_date_mappings(child)


def protocol_paths(candidate_root: Path, output: Path) -> list[Path]:
    paths = set(candidate_root.glob("*protocol.json"))
    paths.update(candidate_root.rglob("protocol.json"))
    paths.update(candidate_root.rglob("*protocol.json"))
    paths.add(candidate_root / "config.json")
    return sorted(
        path for path in paths
        if path.is_file()
        and path.resolve() != output.resolve()
        and "results" not in path.parts
    )


def occupied_footprints(candidate_root: Path, output: Path) -> list[tuple[date, date, str]]:
    occupied: list[tuple[date, date, str]] = []
    for path in protocol_paths(candidate_root, output):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for begin, finish in iter_date_mappings(payload):
            occupied.append(
                (
                    begin - timedelta(days=PRIOR_OPENED_DATA_BUFFER_DAYS),
                    finish,
                    str(path.relative_to(candidate_root)),
                )
            )
    return occupied


def overlaps(left_start: date, left_end: date, right_start: date, right_end: date) -> bool:
    return left_start < right_end and right_start < left_end


def git_blob(path: Path) -> str:
    return subprocess.check_output(["git", "hash-object", str(path)], text=True).strip()


def main() -> None:
    here = Path(__file__).resolve().parent
    candidate_root = here.parent
    strategy_root = candidate_root / STRATEGY_RELATIVE
    output = here / OUTPUT_NAME

    diagnostic = json.loads((strategy_root / "protocol.json").read_text(encoding="utf-8"))
    aggregate = json.loads((strategy_root / "aggregate.json").read_text(encoding="utf-8"))
    if aggregate.get("gate_passed") is not True:
        raise SystemExit("diagnostic gate did not pass; holdout selection forbidden")
    if aggregate.get("success_claim") is not False:
        raise SystemExit("diagnostic evidence must not already claim success")

    expected_blobs = diagnostic["locked_source"]["blobs"]
    actual_blobs = {name: git_blob(strategy_root / name) for name in expected_blobs}
    mismatches = {
        name: {"expected": expected_blobs[name], "actual": actual}
        for name, actual in actual_blobs.items()
        if actual != expected_blobs[name]
    }
    if mismatches:
        raise SystemExit(f"strategy changed after diagnostic freeze: {mismatches}")

    occupied = occupied_footprints(candidate_root, output)
    candidates = [
        RANGE_START + timedelta(days=offset)
        for offset in range((LAST_START - RANGE_START).days + 1)
    ]
    random.Random(SEED).shuffle(candidates)

    selected: list[date] = []
    for start in candidates:
        footprint_start = start - timedelta(days=WARMUP_DAYS)
        end = start + timedelta(days=EVALUATION_DAYS)
        if any(overlaps(footprint_start, end, old_start, old_end) for old_start, old_end, _ in occupied):
            continue
        if any(
            overlaps(
                footprint_start,
                end,
                other - timedelta(days=WARMUP_DAYS),
                other + timedelta(days=EVALUATION_DAYS),
            )
            for other in selected
        ):
            continue
        selected.append(start)
        if len(selected) == 3:
            break
    if len(selected) != 3:
        raise SystemExit("unable to freeze three untouched holdout weeks")

    source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    protocol = {
        "schema": "candidate-11-multi-session-price-discovery-holdout-v1",
        "candidate": diagnostic["candidate"],
        "created_utc": "2026-08-08",
        "validation_mode": "holdout",
        "success_claim": False,
        "market_data_opened": False,
        "source_commit_before_market_data": source_commit,
        "diagnostic_evidence_commit": subprocess.check_output(
            ["git", "log", "-1", "--format=%H", "--", str(strategy_root / "aggregate.json")],
            text=True,
        ).strip(),
        "diagnostic_result": {
            "calendar_days": aggregate["observed_calendar_days"],
            "daily_geometric_growth": aggregate["daily_geometric_growth"],
            "pooled_nav_multiple": aggregate["pooled_nav_multiple"],
            "closed_trades": aggregate["closed_trades"],
            "wins": aggregate["wins"],
            "losses": aggregate["losses"],
            "win_rate": aggregate["win_rate"],
            "payoff_ratio": aggregate["payoff_ratio"],
            "maximum_weekly_closed_trade_drawdown": aggregate["maximum_weekly_closed_trade_drawdown"],
        },
        "locked_source": {
            "strategy_directory": str(STRATEGY_RELATIVE),
            "origin_branch": diagnostic["locked_source"]["origin_branch"],
            "blobs": actual_blobs,
        },
        "selection": {
            "seed": SEED,
            "method": (
                "random.Random(seed).shuffle over every seven-day start from "
                "2023-01-01 through 2025-12-25; first three starts whose "
                "three-day-warmup plus seven-day evaluation footprints do not "
                "overlap the conservatively expanded prior Candidate 11 opened-data "
                "footprints or each other"
            ),
            "warmup_days": WARMUP_DAYS,
            "evaluation_days": EVALUATION_DAYS,
            "prior_opened_data_buffer_days": PRIOR_OPENED_DATA_BUFFER_DAYS,
            "occupied_protocol_files": sorted({source for _, _, source in occupied}),
            "holdouts": {
                f"H{index + 1}": {
                    "start": value.isoformat(),
                    "end_exclusive": (value + timedelta(days=EVALUATION_DAYS)).isoformat(),
                    "role": "untouched-holdout",
                }
                for index, value in enumerate(selected)
            },
        },
        "aggregate_gate": {
            "observed_calendar_days": 21,
            "minimum_daily_geometric_growth": 0.01,
            "minimum_closed_trades": 6,
            "minimum_active_weeks": 2,
            "minimum_win_rate": 0.8,
            "minimum_payoff_ratio": 1.2,
            "maximum_trade_path_drawdown": 0.2,
            "maximum_positive_log_growth_share_from_one_week": 0.7,
            "require_all_safety_audits": True,
            "risk_fraction": 0.03,
            "global_pending_entry_plus_position_limit": 1,
        },
        "decision_rule": {
            "pass": (
                "All precommitted aggregate gates pass on H1-H3 with unchanged source blobs; "
                "then advance unchanged code to a longer contiguous evaluation."
            ),
            "fail": (
                "Reject this frozen variant for the project objective. Do not tune any alpha "
                "threshold or symbol rule on H1-H3."
            ),
        },
        "execution_lock": diagnostic["execution_lock"],
    }

    if output.is_file():
        existing = json.loads(output.read_text(encoding="utf-8"))
        immutable = (
            "schema", "candidate", "validation_mode", "locked_source",
            "selection", "aggregate_gate", "decision_rule", "execution_lock",
        )
        existing_lock = {key: existing.get(key) for key in immutable}
        proposed_lock = {key: protocol.get(key) for key in immutable}
        if existing_lock == proposed_lock:
            print("holdout protocol already frozen")
            return
        # A stricter pre-data selection rule may replace an earlier reservation
        # only while no evaluator/results exist and the committed marker still
        # proves that no holdout market data has been opened.
        results = here / "results"
        evaluator = here / "holdout_runner.py"
        if existing.get("market_data_opened") is False and not results.exists() and not evaluator.exists():
            output.write_text(json.dumps(protocol, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print("replaced pre-data reservation with stricter immutable protocol")
            print(json.dumps(protocol, indent=2, sort_keys=True))
            return
        raise SystemExit("frozen holdout protocol changed after evaluation boundary")

    output.write_text(json.dumps(protocol, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(protocol, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
