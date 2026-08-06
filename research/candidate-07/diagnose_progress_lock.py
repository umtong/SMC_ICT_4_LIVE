#!/usr/bin/env python3
"""Diagnose a target-path equilibrium protection state for candidate-07.

For each completed NautilusTrader trade this script asks only a path question:
after price first delivered 50% of the predeclared entry-to-liquidity-target
range, did it later revisit the 25% quartile before the recorded exit? This is
not an execution or PnL simulation. It supplies evidence before deciding
whether that state transition belongs in the strategy.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from data import load_bundle


NS_PER_MINUTE = 60_000_000_000
TRIGGER_TARGET_FRACTION = 0.50
LOCK_TARGET_FRACTION = 0.25


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _ns_to_iso(value: int | None) -> str | None:
    if value is None:
        return None
    return pd.Timestamp(value, unit="ns", tz="UTC").isoformat()


def _first_delivery(
    *,
    path: pd.DataFrame,
    direction: str,
    trigger_price: float,
) -> int | None:
    for row in path.itertuples():
        if direction == "LONG":
            reached = float(row.high) >= trigger_price
        elif direction == "SHORT":
            reached = float(row.low) <= trigger_price
        else:
            raise ValueError(f"unknown direction: {direction}")
        if reached:
            return int(row.Index.value)
    return None


def _first_lock_revisit(
    *,
    path: pd.DataFrame,
    direction: str,
    lock_price: float,
    after_ns: int,
) -> int | None:
    remaining = path[path.index.view("int64") > after_ns]
    for row in remaining.itertuples():
        if direction == "LONG":
            revisited = float(row.low) <= lock_price
        elif direction == "SHORT":
            revisited = float(row.high) >= lock_price
        else:
            raise ValueError(f"unknown direction: {direction}")
        if revisited:
            return int(row.Index.value)
    return None


def _diagnose_stage(
    *,
    stage_dir: Path,
    config: Mapping[str, Any],
    data_root: Path,
) -> None:
    metrics_path = stage_dir / "metrics.json"
    trades_path = stage_dir / "trades.csv"
    if not metrics_path.is_file() or not trades_path.is_file():
        return
    metrics = _read_json(metrics_path)
    period = metrics.get("period")
    if not isinstance(period, Mapping):
        raise ValueError(f"period missing from {metrics_path}")
    start = date.fromisoformat(str(period["start"]))
    end = date.fromisoformat(str(period["end_exclusive"]))
    bundle = load_bundle(
        symbol=str(config["symbol"]),
        trade_start=start,
        trade_end=end,
        warmup_days=int(config["warmup_days"]),
        cache_root=data_root,
        manifest_destination=stage_dir / "progress_lock_data_manifest.json",
    )
    index_ns = bundle.frame.index.view("int64")
    trades = pd.read_csv(trades_path)
    rows: list[dict[str, Any]] = []

    for trade in trades.itertuples(index=False):
        opened_ns = int(trade.opened_ns)
        closed_ns = int(trade.closed_ns)
        direction = str(trade.direction)
        entry = float(trade.entry_reference)
        target = float(trade.target_price)
        path = bundle.frame[(index_ns >= opened_ns) & (index_ns <= closed_ns)]
        if path.empty:
            raise RuntimeError(f"no bars for {trade.scenario_id}")
        trigger_price = entry + TRIGGER_TARGET_FRACTION * (target - entry)
        lock_price = entry + LOCK_TARGET_FRACTION * (target - entry)
        trigger_ns = _first_delivery(
            path=path,
            direction=direction,
            trigger_price=trigger_price,
        )
        revisit_ns = (
            _first_lock_revisit(
                path=path,
                direction=direction,
                lock_price=lock_price,
                after_ns=trigger_ns,
            )
            if trigger_ns is not None
            else None
        )
        rows.append(
            {
                "scenario_id": str(trade.scenario_id),
                "direction": direction,
                "outcome": "WIN" if float(trade.net_pnl) > 0.0 else "LOSS",
                "net_pnl": float(trade.net_pnl),
                "opened_ns": opened_ns,
                "closed_ns": closed_ns,
                "entry_reference": entry,
                "target_price": target,
                "expected_rr": float(trade.expected_rr),
                "trigger_target_fraction": TRIGGER_TARGET_FRACTION,
                "trigger_price": trigger_price,
                "trigger_ns": trigger_ns,
                "trigger_time": _ns_to_iso(trigger_ns),
                "minutes_to_trigger": (
                    (trigger_ns - opened_ns) / NS_PER_MINUTE if trigger_ns is not None else None
                ),
                "lock_target_fraction": LOCK_TARGET_FRACTION,
                "lock_price": lock_price,
                "lock_revisit_ns": revisit_ns,
                "lock_revisit_time": _ns_to_iso(revisit_ns),
                "minutes_from_trigger_to_lock_revisit": (
                    (revisit_ns - trigger_ns) / NS_PER_MINUTE
                    if revisit_ns is not None and trigger_ns is not None
                    else None
                ),
                "triggered": trigger_ns is not None,
                "lock_revisited_after_trigger": revisit_ns is not None,
            },
        )

    frame = pd.DataFrame(rows)
    frame.to_csv(stage_dir / "progress_lock_diagnostic.csv", index=False)
    triggered = frame[frame["triggered"]]
    revisited = frame[frame["lock_revisited_after_trigger"]]
    winners = frame[frame["outcome"] == "WIN"]
    losers = frame[frame["outcome"] == "LOSS"]
    payload = {
        "stage": str(metrics["stage"]),
        "trades": int(len(frame.index)),
        "triggered": int(len(triggered.index)),
        "revisited_lock_after_trigger": int(len(revisited.index)),
        "winning_trades_triggered": int(winners["triggered"].sum()),
        "winning_trades_revisiting_lock": int(winners["lock_revisited_after_trigger"].sum()),
        "losing_trades_triggered": int(losers["triggered"].sum()),
        "losing_trades_revisiting_lock": int(losers["lock_revisited_after_trigger"].sum()),
        "definition": {
            "trigger": "50% of predeclared entry-to-target range delivered",
            "lock": "25% quartile of the same range",
            "bar_observation": "completed checksum-verified one-minute OHLCV",
            "counterfactual_execution": False,
        },
    }
    (stage_dir / "progress_lock_summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run(args: argparse.Namespace) -> int:
    config = _read_json(args.config.resolve())
    output = args.output.resolve()
    data_root = args.data_root.resolve()
    for stage_dir in sorted(path for path in output.glob("week-*") if path.is_dir()):
        _diagnose_stage(stage_dir=stage_dir, config=config, data_root=data_root)
    return 0


def build_parser() -> argparse.ArgumentParser:
    candidate_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=candidate_dir / "config.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=Path(".research-data/candidate-07"))
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
