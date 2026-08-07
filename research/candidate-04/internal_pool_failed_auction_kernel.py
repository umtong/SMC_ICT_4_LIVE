#!/usr/bin/env python3
"""V57: the portable internal-pool failed-auction kernel.

V56 failed prospectively because its trapped-inventory and external-discovery
routes both lost.  The internal-pool route did not occur in that week and has a
separate market cause: a sub-ATR internal liquidity pool is swept, rejected and
followed by a pre-sweep structure break toward a pre-existing external target.
This module retains only that mechanism.  It changes no entry, stop, target,
feature, cost, risk or execution rule.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any

import pandas as pd

import prominence_state_router_for_symbol as symbol_router
import prominence_state_router as base


SCENARIO = base.NORMAL_FAILED_AUCTION


def select(
    rows: list[dict[str, Any]],
    rich: pd.DataFrame,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for row in rows:
        counts["input_signals"] += 1
        if str(row.get("scenario")) != SCENARIO:
            counts["non_kernel_scenario"] += 1
            continue
        routed, reason = base.route_signal(row, rich)
        counts[reason] += 1
        if routed is None:
            counts["discarded"] += 1
            continue
        kept.append(routed)
        counts["routed"] += 1
    kept.sort(key=lambda item: int(item["observe_time_ns"]))
    return kept, {
        "candidate": "candidate-04-v57-internal-pool-failed-auction",
        "compiler": "candidate-04-v57-internal-pool-kernel-v1",
        "scenario": SCENARIO,
        "counts": dict(counts),
        "written_signals": len(kept),
        "market_logic_changed_after_results": False,
        "future_information_used": False,
        "performance_calculated": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--rich-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = json.loads(args.signals.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise RuntimeError("signals must be a JSON list")
    rich = symbol_router.load_symbol_rich(args.rich_dir)
    rows, summary = select(
        [dict(item) for item in value if isinstance(item, dict)], rich
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "signals.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
