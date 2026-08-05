#!/usr/bin/env python3
"""Attach official futures positioning states to failed-auction outcomes.

Price/taker-flow bars cannot distinguish three economically different probes:

* forced position liquidation and exhaustion,
* new positions trapped outside value, and
* ordinary price noise with little inventory transfer.

This diagnostic joins the exact candidate plans to Binance Vision five-minute
open interest/position-ratio metrics and one-minute premium/mark indices using
backward, causal as-of joins.  It reports predefined economic state categories
on the 2024 development interval and frozen quick weeks; it does not change the
production strategy.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from auxiliary_data import (  # noqa: E402
    download_auxiliary,
    merge_auxiliary_at_times,
    read_index_klines,
    read_metrics,
)
from core import AuctionStateMachine, CandidateConfig  # noqa: E402
from data import load_interval, parse_utc_date, to_auction_bars  # noqa: E402
from portfolio_probe import Variant, simulate  # noqa: E402


AUX_TYPES = ("metrics", "premiumIndexKlines", "markPriceKlines")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _segments(research: dict[str, Any]) -> list[tuple[str, datetime, datetime, str]]:
    def week(label: str, value: str) -> tuple[str, datetime, datetime, str]:
        start = parse_utc_date(value)
        return label, start, start + timedelta(days=7), "quick"

    return [
        week("discovery", str(research["discovery_week"])),
        *[
            week(f"confirmation-{index + 1}", value)
            for index, value in enumerate(research["confirmation_weeks"])
        ],
        (
            "long-evaluation",
            parse_utc_date(str(research["long_start"])),
            parse_utc_date(str(research["long_end"])),
            "development",
        ),
    ]


def _event_context(bars: list[Any], candidate: CandidateConfig) -> pd.DataFrame:
    machine = AuctionStateMachine(
        candidate,
        instrument_id=f"BTCUSDT-PERP.BINANCE:{candidate.range_minutes}m",
    )
    for item in bars:
        machine.on_bar(item)
    rows: dict[str, dict[str, Any]] = {}
    for event in machine.transitions:
        row = rows.setdefault(event.scenario_id, {"scenario_id": event.scenario_id})
        if event.event_type == "LIQUIDITY_PROBE_REJECTED":
            row.update(
                {
                    "probe_time_ns": event.event_time_ns,
                    "probe_flow_z": event.details.get("flow_z"),
                    "probe_volume_z": event.details.get("volume_z"),
                    "probe_atr": event.details.get("atr"),
                    "probe_boundary": event.details.get("boundary"),
                    "probe_extreme": event.details.get("excursion_extreme"),
                },
            )
        elif event.event_type == "REVERSAL_DISPLACEMENT_CONFIRMED":
            row.update(
                {
                    "displacement_time_ns": event.event_time_ns,
                    "displacement_flow_z": event.details.get("flow_z"),
                    "displacement_body_atr": event.details.get("body_atr"),
                    "structure_overshoot_atr": event.details.get("structure_overshoot_atr"),
                },
            )
    return pd.DataFrame(rows.values())


def _prefix_auxiliary(
    times: pd.Series,
    *,
    prefix: str,
    metrics: pd.DataFrame,
    premium: pd.DataFrame,
    mark: pd.DataFrame,
) -> pd.DataFrame:
    joined = merge_auxiliary_at_times(times, metrics=metrics, premium=premium, mark=mark)
    joined = joined.drop(columns=[column for column in ("event_row", "event_time", "symbol") if column in joined])
    joined = joined.rename(columns={column: f"{prefix}_{column}" for column in joined.columns})
    return joined.reset_index(drop=True)


def _rules(frame: pd.DataFrame) -> dict[str, pd.Series]:
    premium = frame["sweep_aligned_premium_z"]
    oi_event = frame["oi_probe_to_displacement"]
    oi_z = frame["displacement_oi_pct_15_z"]
    reversal_flow = frame["trade_direction_displacement_flow_z"]
    top_crowd = frame["sweep_aligned_top_account_z"]
    return {
        "all": pd.Series(True, index=frame.index),
        "absolute-oi-transfer-0.20pct": oi_event.abs() >= 0.002,
        "absolute-oi-transfer-0.50pct": oi_event.abs() >= 0.005,
        "liquidation-exhaustion": (oi_event <= -0.002) & (premium >= 0.50),
        "trapped-new-positions": (oi_event >= 0.002) & (premium >= 0.50),
        "positioning-shock-with-premium": (oi_event.abs() >= 0.002) & (premium >= 0.50),
        "premium-extreme": premium >= 1.00,
        "oi-change-z-extreme": oi_z.abs() >= 1.50,
        "crowding-and-premium": (premium >= 0.75) & (top_crowd >= 0.50),
        "strong-reversal-flow": reversal_flow >= 1.70,
        "strong-flow-or-positioning-shock": (
            (reversal_flow >= 1.70)
            | ((oi_event.abs() >= 0.002) & (premium >= 0.50))
        ),
        "strong-flow-and-positioning-shock": (
            (reversal_flow >= 1.25)
            & (oi_event.abs() >= 0.002)
            & (premium >= 0.50)
        ),
    }


def _score(frame: pd.DataFrame, role: str) -> list[dict[str, Any]]:
    source = frame.loc[frame["role"] == role].copy()
    rows: list[dict[str, Any]] = []
    for name, mask in _rules(source).items():
        values = pd.to_numeric(source.loc[mask, "realized_r"], errors="coerce").dropna()
        gross_profit = float(values[values > 0.0].sum())
        gross_loss = abs(float(values[values < 0.0].sum()))
        selected = source.loc[mask]
        quarter_rows = []
        if role == "development" and not selected.empty:
            for quarter, group in selected.groupby(selected["entry_dt"].dt.quarter):
                quarter_rows.append(
                    {
                        "quarter": int(quarter),
                        "trades": int(len(group)),
                        "sum_r": float(group["realized_r"].sum()),
                    },
                )
        rows.append(
            {
                "role": role,
                "rule": name,
                "trades": int(len(values)),
                "sum_r": float(values.sum()),
                "mean_r": float(values.mean()) if len(values) else None,
                "win_rate": float((values > 0.0).mean()) if len(values) else None,
                "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else None,
                "quarters": quarter_rows,
            },
        )
    return rows


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    candidate = CandidateConfig.from_mapping(raw["candidate"])
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    combined_rows: list[pd.DataFrame] = []
    manifest_rows: list[dict[str, Any]] = []

    for label, start, end, role in _segments(research):
        frame, _ = load_interval(
            symbol="BTCUSDT",
            start=start,
            end=end,
            cache_dir=args.cache / "klines",
            warmup_minutes=max(int(research.get("warmup_minutes", 420)), candidate.range_minutes + 180),
        )
        bars = to_auction_bars(frame)
        trades, _, _ = simulate(
            variant=Variant("btc-240", ("BTCUSDT",), (candidate.range_minutes,)),
            bars_by_symbol={"BTCUSDT": bars},
            evaluation_start=start,
            evaluation_end=end,
            base_candidate=candidate,
            cost=float(execution["all_in_cost_bps_per_side"]) / 10_000.0,
            minimum_price_risk_fraction=float(execution["minimum_price_risk_fraction"]),
            minimum_net_reward_risk=float(execution["minimum_net_reward_risk"]),
            starting_nav=float(execution["starting_nav"]),
            risk_rates=(0.01,),
        )
        if trades.empty:
            continue
        context = _event_context(bars, candidate)
        trades = trades.merge(context, on="scenario_id", how="left", validate="one_to_one")

        aux_start = start - timedelta(days=2)
        records = download_auxiliary(
            data_types=AUX_TYPES,
            symbol="BTCUSDT",
            start=aux_start,
            end=end,
            cache_dir=args.cache / "auxiliary",
            workers=args.workers,
        )
        manifest_rows.extend(record.to_dict() for record in records)
        metrics = read_metrics(records)
        premium = read_index_klines(records, data_type="premiumIndexKlines", prefix="premium")
        mark = read_index_klines(records, data_type="markPriceKlines", prefix="mark")

        parts = [trades.reset_index(drop=True)]
        for prefix, column in (
            ("probe", "probe_time_ns"),
            ("displacement", "displacement_time_ns"),
            ("entry_aux", "entry_time_ns"),
        ):
            parts.append(
                _prefix_auxiliary(
                    trades[column].astype("int64"),
                    prefix=prefix,
                    metrics=metrics,
                    premium=premium,
                    mark=mark,
                ),
            )
        joined = pd.concat(parts, axis=1)
        joined.insert(0, "segment", label)
        joined.insert(1, "role", role)
        joined["entry_dt"] = pd.to_datetime(joined["entry_time_ns"], unit="ns", utc=True)
        side_sign = joined["side"].map({"LONG": 1.0, "SHORT": -1.0}).astype(float)
        sweep_sign = -side_sign
        joined["trade_direction_displacement_flow_z"] = joined["displacement_flow_z"] * side_sign
        joined["oi_probe_to_displacement"] = (
            joined["displacement_sum_open_interest"] / joined["probe_sum_open_interest"] - 1.0
        )
        joined["oi_probe_to_entry"] = (
            joined["entry_aux_sum_open_interest"] / joined["probe_sum_open_interest"] - 1.0
        )
        joined["sweep_aligned_premium_z"] = joined["probe_premium_z_120"] * sweep_sign
        joined["displacement_aligned_premium_z"] = joined["displacement_premium_z_120"] * side_sign
        joined["sweep_aligned_top_account_z"] = (
            joined["probe_count_toptrader_long_short_ratio_z"] * sweep_sign
        )
        joined["sweep_aligned_global_account_z"] = (
            joined["probe_count_long_short_ratio_z"] * sweep_sign
        )
        joined["trade_vs_mark_at_entry_bps"] = (
            (joined["entry"] / joined["entry_aux_mark_close"] - 1.0) * 10_000.0
        )
        destination = output / label
        destination.mkdir(parents=True, exist_ok=True)
        joined.to_csv(destination / "trades_with_positioning.csv", index=False)
        combined_rows.append(joined)

    combined = pd.concat(combined_rows, ignore_index=True)
    combined.to_csv(output / "combined_trades_with_positioning.csv", index=False)
    scores = _score(combined, "development") + _score(combined, "quick")
    pd.DataFrame(scores).to_csv(output / "positioning_rule_scores.csv", index=False)
    # Remove duplicate cached records from segments which overlap auxiliary days.
    manifest = pd.DataFrame(manifest_rows).drop_duplicates(["data_type", "symbol", "day"])
    _atomic_json(
        output / "auxiliary_manifest.json",
        {
            "provider": "Binance Vision",
            "files": manifest.to_dict(orient="records"),
        },
    )
    summary = {
        "trades": int(len(combined)),
        "development_trades": int((combined["role"] == "development").sum()),
        "quick_trades": int((combined["role"] == "quick").sum()),
        "rules": scores,
    }
    _atomic_json(output / "positioning_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-positioning")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-positioning")
    parser.add_argument("--workers", type=int, default=24)
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
