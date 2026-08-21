#!/usr/bin/env python3
"""Fast real-data clinic for the skilled response primitive.

This intentionally bypasses the expensive semantic and diagonal level builders and
presents only causal multi-scale directional-change extremes to the exact same
response, entry, stop, target and order-resolution policy.  It is a cheap market-logic
clinic, not the complete candidate and not a substitute for its four-market workflow.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path

import pandas as pd

import event_time_auction_harvest as event_time
import skilled_liquidity_policy as policy


loader = event_time.loader


def run_research(
    *,
    start: str,
    end: str,
    warmup_days: int,
    symbols: list[str],
    cache: Path,
    output: Path,
) -> dict[str, object]:
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    raw = loader._load_universe(
        start_date - timedelta(days=warmup_days),
        end_date + timedelta(days=3),
        symbols,
        cache,
    )
    prepared = loader._prepare_state(raw)
    end_timestamp = pd.Timestamp(end)
    end_timestamp = (
        end_timestamp.tz_localize("UTC")
        if end_timestamp.tzinfo is None
        else end_timestamp.tz_convert("UTC")
    )
    policy.dlp2.base_policy.fixed._DECISION_END_NS = int(end_timestamp.value)

    output.mkdir(parents=True, exist_ok=True)
    frames: list[pd.DataFrame] = []
    per_symbol: dict[str, dict[str, int]] = {}
    for symbol in symbols:
        frame, counts = policy.generate_symbol(
            symbol,
            prepared[symbol],
            [],
            {},
            start,
        )
        frames.append(frame)
        exists = (
            frame.get("order_exists", pd.Series(False, index=frame.index))
            .astype(str)
            .str.lower()
            .isin({"true", "1", "yes"})
        )
        per_symbol[symbol] = {
            **{str(key): int(value) for key, value in counts.items()},
            "episode_rows": int(len(frame)),
            "order_rows": int(exists.sum()),
        }
    actions = (
        pd.concat(frames, ignore_index=True, sort=False)
        if frames
        else pd.DataFrame()
    )
    actions.to_csv(
        output / "departure_actions.csv.gz",
        index=False,
        compression="gzip",
    )
    summary: dict[str, object] = {
        "start": start,
        "end": end,
        "warmup_days": int(warmup_days),
        "symbols": symbols,
        "policy_version": "skilled-liquidity-response-v1",
        "clinic": "FAST_DIRECTIONAL_CHANGE_BOUNDARIES_ONLY",
        "complete_semantic_candidate": False,
        "same_response_and_plan_policy_as_complete_candidate": True,
        "episode_rows": int(len(actions)),
        "order_rows": int(
            actions.get("order_exists", pd.Series(False, index=actions.index))
            .astype(str)
            .str.lower()
            .isin({"true", "1", "yes"})
            .sum()
        ),
        "per_symbol": per_symbol,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--warmup-days", type=int, default=14)
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
    )
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            run_research(
                start=args.start,
                end=args.end,
                warmup_days=args.warmup_days,
                symbols=list(args.symbols),
                cache=args.cache,
                output=args.output,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
