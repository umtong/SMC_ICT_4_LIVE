#!/usr/bin/env python3
"""Extract causal post-confirmation price paths for candidate-07 diagnostics.

This script performs no fills, order matching, position accounting, PnL
calculation, or counterfactual backtest. It only joins completed NautilusTrader
trade reports to the original checksum-verified one-minute market bars so entry
timing failures can be inspected without guessing from terminal PnL.
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
RETEST_FRACTIONS = (0.25, 0.50, 0.75)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError(f"non-object event at {path}:{line_number}")
            events.append(payload)
    return events


def _first_event(
    events: list[dict[str, Any]],
    scenario_id: str,
    reason_code: str,
) -> dict[str, Any]:
    for event in events:
        if event.get("scenario_id") == scenario_id and event.get("reason_code") == reason_code:
            return event
    raise RuntimeError(f"missing {reason_code} event for {scenario_id}")


def _ns_to_iso(value: int | None) -> str | None:
    if value is None:
        return None
    return pd.Timestamp(value, unit="ns", tz="UTC").isoformat()


def _first_retest(
    *,
    path: pd.DataFrame,
    direction: str,
    level: float,
) -> tuple[int | None, int | None]:
    first_touch: int | None = None
    first_rejection: int | None = None
    for row in path.itertuples():
        ts_ns = int(row.Index.value)
        if direction == "SHORT":
            touched = float(row.high) >= level
            rejected = touched and float(row.close) < level and float(row.close) < float(row.open)
        elif direction == "LONG":
            touched = float(row.low) <= level
            rejected = touched and float(row.close) > level and float(row.close) > float(row.open)
        else:
            raise ValueError(f"unknown direction: {direction}")
        if touched and first_touch is None:
            first_touch = ts_ns
        if rejected:
            first_rejection = ts_ns
            break
    return first_touch, first_rejection


def _diagnose_stage(
    *,
    stage_dir: Path,
    config: Mapping[str, Any],
    data_root: Path,
) -> None:
    trades_path = stage_dir / "trades.csv"
    events_path = stage_dir / "events.jsonl"
    metrics_path = stage_dir / "metrics.json"
    if not trades_path.is_file() or not events_path.is_file() or not metrics_path.is_file():
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
        manifest_destination=stage_dir / "diagnostic_data_manifest.json",
    )
    events = _read_events(events_path)
    trades = pd.read_csv(trades_path)
    path_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []

    for trade in trades.itertuples(index=False):
        scenario_id = str(trade.scenario_id)
        direction = str(trade.direction)
        contact = _first_event(events, scenario_id, "UPPER_POOL_SWEEP_RECLAIM" if direction == "SHORT" else "LOWER_POOL_SWEEP_RECLAIM")
        ready = _first_event(events, scenario_id, "CAUSAL_ROUTE_READY")
        confirm_ns = int(ready["event_time_ns"])
        opened_ns = int(trade.opened_ns)
        closed_ns = int(trade.closed_ns)
        liquidity = float(contact["reference_price"])
        confirm_close = float(ready["reference_price"])
        path_end_ns = min(closed_ns + 5 * NS_PER_MINUTE, confirm_ns + 30 * NS_PER_MINUTE)
        path = bundle.frame[
            (bundle.frame.index.view("int64") > confirm_ns)
            & (bundle.frame.index.view("int64") <= path_end_ns)
        ]
        entry_window = bundle.frame[
            (bundle.frame.index.view("int64") > confirm_ns)
            & (bundle.frame.index.view("int64") <= confirm_ns + 15 * NS_PER_MINUTE)
        ]
        summary: dict[str, Any] = {
            "scenario_id": scenario_id,
            "direction": direction,
            "net_pnl": float(trade.net_pnl),
            "net_return_on_nav": float(trade.net_return_on_nav),
            "confirmation_ns": confirm_ns,
            "confirmation_time": _ns_to_iso(confirm_ns),
            "opened_ns": opened_ns,
            "opened_time": _ns_to_iso(opened_ns),
            "closed_ns": closed_ns,
            "closed_time": _ns_to_iso(closed_ns),
            "liquidity_level": liquidity,
            "confirmation_close": confirm_close,
            "entry_reference": float(trade.entry_reference),
            "stop_price": float(trade.stop_price),
            "target_price": float(trade.target_price),
            "expected_rr": float(trade.expected_rr),
        }
        for fraction in RETEST_FRACTIONS:
            level = confirm_close + fraction * (liquidity - confirm_close)
            first_touch, first_rejection = _first_retest(
                path=entry_window,
                direction=direction,
                level=level,
            )
            label = f"retest_{int(fraction * 100):02d}"
            summary[f"{label}_level"] = level
            summary[f"{label}_first_touch_ns"] = first_touch
            summary[f"{label}_first_touch_time"] = _ns_to_iso(first_touch)
            summary[f"{label}_first_touch_delay_minutes"] = (
                (first_touch - confirm_ns) / NS_PER_MINUTE if first_touch is not None else None
            )
            summary[f"{label}_first_rejection_ns"] = first_rejection
            summary[f"{label}_first_rejection_time"] = _ns_to_iso(first_rejection)
            summary[f"{label}_first_rejection_delay_minutes"] = (
                (first_rejection - confirm_ns) / NS_PER_MINUTE if first_rejection is not None else None
            )

        for row in path.itertuples():
            ts_ns = int(row.Index.value)
            path_rows.append(
                {
                    "scenario_id": scenario_id,
                    "direction": direction,
                    "net_pnl": float(trade.net_pnl),
                    "confirmation_ns": confirm_ns,
                    "timestamp_ns": ts_ns,
                    "timestamp": _ns_to_iso(ts_ns),
                    "minutes_after_confirmation": (ts_ns - confirm_ns) / NS_PER_MINUTE,
                    "open": float(row.open),
                    "high": float(row.high),
                    "low": float(row.low),
                    "close": float(row.close),
                    "volume": float(row.volume),
                    "liquidity_level": liquidity,
                    "confirmation_close": confirm_close,
                    "stop_price": float(trade.stop_price),
                    "target_price": float(trade.target_price),
                },
            )
        summaries.append(summary)

    pd.DataFrame(summaries).to_csv(stage_dir / "entry_retest_summary.csv", index=False)
    pd.DataFrame(path_rows).to_csv(stage_dir / "entry_paths.csv", index=False)


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
