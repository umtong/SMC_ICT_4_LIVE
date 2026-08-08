#!/usr/bin/env python3
"""V58b target-contract repair for parent-state micro auctions.

The opposite frozen-balance boundary is the first structural checkpoint of a
failed micro auction, but with the unchanged 7.5 bps each-side cost contract it
may not provide 1.2 net-R.  Treating it as a final target made every V58 intent
unexecutable.  V58b preserves the exact V58 entries, sides, stops and state
routing, records that boundary as a checkpoint, and leaves the final destination
to the unchanged causal external-liquidity registry.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import micro_auction_parent_state_router as base


TARGET_KEYS = (
    "causal_target_reference",
    "causal_target_source",
    "causal_target_observed_index",
)


def route_signals(
    signals: list[dict[str, Any]],
    rich,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    routed, summary = base.route_signals(signals, rich)
    checkpoint_count = 0
    for row in routed:
        details = dict(row.get("details") or {})
        if details.get("causal_target_source") == base.TARGET_SOURCE:
            details["first_balance_checkpoint"] = details.get(
                "causal_target_reference"
            )
            details["first_balance_checkpoint_observed_index"] = details.get(
                "causal_target_observed_index"
            )
            details["first_balance_checkpoint_contract"] = (
                "failed micro auction must first traverse the completed balance; "
                "final target remains pre-existing external liquidity"
            )
            for key in TARGET_KEYS:
                details.pop(key, None)
            details["v58b_target_contract"] = (
                "checkpoint_not_final_target; use strict causal registry"
            )
            row["details"] = details
            checkpoint_count += 1
    summary = dict(summary)
    summary["candidate"] = "candidate-04-v58b-parent-state-micro-auction"
    summary["compiler"] = "candidate-04-v58b-parent-state-router-v2"
    summary["balance_checkpoint_signals"] = checkpoint_count
    summary["target_contract"] = (
        "opposite frozen balance boundary is a checkpoint; final destination "
        "must come from pre-existing rolling/session/day/week/pivot liquidity"
    )
    return routed, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--rich-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--symbol", default="BTCUSDT")
    args = parser.parse_args()
    raw = json.loads(args.signals.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise RuntimeError("signals must contain a JSON list")
    rich = base.load_rich(args.rich_dir, args.symbol)
    routed, summary = route_signals(
        [dict(item) for item in raw if isinstance(item, dict)], rich
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "signals.json").write_text(
        json.dumps(routed, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
