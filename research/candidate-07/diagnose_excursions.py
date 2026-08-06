#!/usr/bin/env python3
"""Measure full-hold favorable/adverse excursions for candidate-07 trades.

The script is diagnostic only. It joins completed NautilusTrader trade records
to checksum-verified one-minute bars and reports observed path statistics. It
never creates orders, chooses fills, simulates a portfolio, or calculates a
counterfactual strategy return.
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
FAVORABLE_THRESHOLDS_R = (0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0)
ADVERSE_THRESHOLDS_R = (0.5, 1.0)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _ns_to_iso(value: int | None) -> str | None:
    if value is None:
        return None
    return pd.Timestamp(value, unit="ns", tz="UTC").isoformat()


def _first_threshold_time(
    *,
    path: pd.DataFrame,
    direction: str,
    entry: float,
    risk: float,
    threshold_r: float,
    favorable: bool,
) -> int | None:
    distance = risk * threshold_r
    for row in path.itertuples():
        if direction == "LONG":
            reached = (
                float(row.high) >= entry + distance
                if favorable
                else float(row.low) <= entry - distance
            )
        elif direction == "SHORT":
            reached = (
                float(row.low) <= entry - distance
                if favorable
                else float(row.high) >= entry + distance
            )
        else:
            raise ValueError(f"unknown direction: {direction}")
        if reached:
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
        manifest_destination=stage_dir / "excursion_data_manifest.json",
    )
    index_ns = bundle.frame.index.view("int64")
    trades = pd.read_csv(trades_path)
    rows: list[dict[str, Any]] = []

    for trade in trades.itertuples(index=False):
        opened_ns = int(trade.opened_ns)
        closed_ns = int(trade.closed_ns)
        direction = str(trade.direction)
        entry = float(trade.entry_reference)
        stop = float(trade.stop_price)
        target = float(trade.target_price)
        risk = abs(entry - stop)
        if risk <= 0.0:
            raise ValueError(f"non-positive risk for {trade.scenario_id}")
        path = bundle.frame[(index_ns >= opened_ns) & (index_ns <= closed_ns)]
        if path.empty:
            raise RuntimeError(
                f"no one-minute bars for {trade.scenario_id}: {opened_ns} -> {closed_ns}",
            )

        if direction == "LONG":
            favorable_series = (path["high"] - entry) / risk
            adverse_series = (entry - path["low"]) / risk
            close_series = (path["close"] - entry) / risk
        elif direction == "SHORT":
            favorable_series = (entry - path["low"]) / risk
            adverse_series = (path["high"] - entry) / risk
            close_series = (entry - path["close"]) / risk
        else:
            raise ValueError(f"unknown direction: {direction}")

        mfe_index = favorable_series.idxmax()
        mae_index = adverse_series.idxmax()
        record: dict[str, Any] = {
            "scenario_id": str(trade.scenario_id),
            "direction": direction,
            "net_pnl": float(trade.net_pnl),
            "net_return_on_nav": float(trade.net_return_on_nav),
            "outcome": "WIN" if float(trade.net_pnl) > 0.0 else "LOSS",
            "opened_ns": opened_ns,
            "opened_time": _ns_to_iso(opened_ns),
            "closed_ns": closed_ns,
            "closed_time": _ns_to_iso(closed_ns),
            "hold_minutes": (closed_ns - opened_ns) / NS_PER_MINUTE,
            "entry_reference": entry,
            "stop_price": stop,
            "target_price": target,
            "risk_price": risk,
            "expected_rr": float(trade.expected_rr),
            "mfe_r": float(favorable_series.max()),
            "mfe_ns": int(mfe_index.value),
            "mfe_time": mfe_index.isoformat(),
            "minutes_to_mfe": (int(mfe_index.value) - opened_ns) / NS_PER_MINUTE,
            "mae_r": float(adverse_series.max()),
            "mae_ns": int(mae_index.value),
            "mae_time": mae_index.isoformat(),
            "minutes_to_mae": (int(mae_index.value) - opened_ns) / NS_PER_MINUTE,
            "max_close_r": float(close_series.max()),
            "min_close_r": float(close_series.min()),
            "terminal_close_r": float(close_series.iloc[-1]),
            "bars_observed": int(len(path.index)),
        }

        for threshold in FAVORABLE_THRESHOLDS_R:
            threshold_ns = _first_threshold_time(
                path=path,
                direction=direction,
                entry=entry,
                risk=risk,
                threshold_r=threshold,
                favorable=True,
            )
            label = str(threshold).replace(".", "_")
            record[f"first_favorable_{label}r_ns"] = threshold_ns
            record[f"first_favorable_{label}r_time"] = _ns_to_iso(threshold_ns)
            record[f"minutes_to_favorable_{label}r"] = (
                (threshold_ns - opened_ns) / NS_PER_MINUTE if threshold_ns is not None else None
            )
        for threshold in ADVERSE_THRESHOLDS_R:
            threshold_ns = _first_threshold_time(
                path=path,
                direction=direction,
                entry=entry,
                risk=risk,
                threshold_r=threshold,
                favorable=False,
            )
            label = str(threshold).replace(".", "_")
            record[f"first_adverse_{label}r_ns"] = threshold_ns
            record[f"first_adverse_{label}r_time"] = _ns_to_iso(threshold_ns)
            record[f"minutes_to_adverse_{label}r"] = (
                (threshold_ns - opened_ns) / NS_PER_MINUTE if threshold_ns is not None else None
            )
        rows.append(record)

    frame = pd.DataFrame(rows)
    frame.to_csv(stage_dir / "trade_excursions.csv", index=False)
    losers = frame[frame["outcome"] == "LOSS"]
    winners = frame[frame["outcome"] == "WIN"]
    summary = {
        "stage": str(metrics["stage"]),
        "trades": int(len(frame.index)),
        "winners": int(len(winners.index)),
        "losers": int(len(losers.index)),
        "winner_median_mfe_r": float(winners["mfe_r"].median()) if not winners.empty else None,
        "loser_median_mfe_r": float(losers["mfe_r"].median()) if not losers.empty else None,
        "losers_reaching_at_least_0_5r": int((losers["mfe_r"] >= 0.5).sum()),
        "losers_reaching_at_least_1r": int((losers["mfe_r"] >= 1.0).sum()),
        "losers_reaching_at_least_1_5r": int((losers["mfe_r"] >= 1.5).sum()),
        "losers_reaching_at_least_2r": int((losers["mfe_r"] >= 2.0).sum()),
        "losers_reaching_at_least_3r": int((losers["mfe_r"] >= 3.0).sum()),
        "winner_median_mae_r": float(winners["mae_r"].median()) if not winners.empty else None,
        "loser_median_mae_r": float(losers["mae_r"].median()) if not losers.empty else None,
        "diagnostic_contract": {
            "execution_engine": "none; reads completed NautilusTrader output only",
            "market_data": "checksum-verified one-minute bars",
            "counterfactual_pnl": False,
        },
    }
    _write_json(stage_dir / "excursion_summary.json", summary)


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
