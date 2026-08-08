#!/usr/bin/env python3
"""Diagnose variable-horizon aggressor-flow transfer in initiative entries.

This is the corrected successor to ``diagnose_initiative_flow_transition``.
The initiative candidate can confirm MSS one, two, or three completed five-minute
bars after the source sweep.  Therefore the confirmation flow interval must span
the exact causal interval actually used by the strategy, not an assumed single
five-minute bar.  The script creates no signals, orders, fills, PnL, or NAV.
Outcomes are joined only after all pre-entry features are computed.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import json
from math import prod
from pathlib import Path
from statistics import median
from typing import Any, Mapping

import pandas as pd

import diagnose_initiative_flow_transition as base
from data_flow import load_flow_bundle
from smc_ict_4.manifest import write_json_atomic


NS_PER_MINUTE = base.NS_PER_MINUTE
NS_PER_FIVE_MINUTES = base.NS_PER_FIVE_MINUTES


def completed_minutes_between(start_ns: int, end_ns: int) -> int:
    """Return exact completed one-minute bars between two close timestamps."""
    delta = int(end_ns) - int(start_ns)
    if delta <= 0:
        raise ValueError("end timestamp must follow start timestamp")
    if delta % NS_PER_MINUTE != 0:
        raise RuntimeError(
            "causal timestamps are not aligned to a whole completed minute: "
            f"start={start_ns}, end={end_ns}, delta={delta}"
        )
    return delta // NS_PER_MINUTE


def _feature_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    numeric = (
        "confirmation_bars",
        "source_attack_imbalance",
        "confirmation_reversal_imbalance",
        "confirmation_reversal_minute_fraction",
        "confirmation_terminal_reversal_imbalance",
        "entry_reversal_imbalance",
        "confirmation_to_source_directional_volume",
        "confirmation_residual_attack_ratio",
        "confirmation_reversal_price_move_atr",
        "confirmation_reversal_path_efficiency",
    )
    output: dict[str, Any] = {}
    for name in numeric:
        winners = [float(row[name]) for row in rows if bool(row["win"])]
        losses = [float(row[name]) for row in rows if not bool(row["win"])]
        output[name] = {
            "winner_count": len(winners),
            "loss_count": len(losses),
            "winner_median": median(winners) if winners else None,
            "loss_median": median(losses) if losses else None,
            "median_difference_winner_minus_loss": (
                median(winners) - median(losses)
                if winners and losses
                else None
            ),
        }
    return output


def _rule_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    names = (
        "source_attack_flow_present",
        "confirmation_flow_sign_flip",
        "persistent_confirmation_transfer",
        "entry_minute_confirms_transfer",
    )
    output: dict[str, Any] = {}
    for name in names:
        selected = [row for row in rows if bool(row[name])]
        returns = [float(row["net_return_on_nav"]) for row in selected]
        by_stage: dict[str, dict[str, Any]] = {}
        for stage in sorted({str(row["stage"]) for row in rows}):
            subset = [row for row in selected if str(row["stage"]) == stage]
            by_stage[stage] = {
                "selected": len(subset),
                "wins": sum(bool(row["win"]) for row in subset),
                "losses": sum(not bool(row["win"]) for row in subset),
            }
        by_direction: dict[str, dict[str, Any]] = {}
        for direction in ("LONG", "SHORT"):
            subset = [row for row in selected if str(row["direction"]) == direction]
            by_direction[direction] = {
                "selected": len(subset),
                "wins": sum(bool(row["win"]) for row in subset),
                "losses": sum(not bool(row["win"]) for row in subset),
            }
        output[name] = {
            "selected": len(selected),
            "wins": sum(bool(row["win"]) for row in selected),
            "losses": sum(not bool(row["win"]) for row in selected),
            "win_rate": (
                sum(bool(row["win"]) for row in selected) / len(selected)
                if selected
                else 0.0
            ),
            "diagnostic_compounded_recorded_returns": (
                prod(1.0 + value for value in returns) - 1.0
                if returns
                else 0.0
            ),
            "by_stage": by_stage,
            "by_direction": by_direction,
            "note": (
                "diagnostic only; exact counterfactual NAV requires a fresh "
                "NautilusTrader replay with the selected rule frozen"
            ),
        }
    return output


def diagnose(args: argparse.Namespace) -> int:
    run_root = args.run_root.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    bundle = load_flow_bundle(
        symbol=str(config["symbol"]),
        trade_start=args.start,
        trade_end=args.end,
        warmup_days=int(config["warmup_days"]),
        cache_root=args.data_root.resolve(),
        manifest_destination=output / "flow_data_manifest.json",
    )
    flow = base.prepare_flow_frame(bundle.frame)
    events = base._read_events(run_root / "events.jsonl")
    trades = pd.read_csv(run_root / "trades.csv")
    absorption = trades[trades["kind"] == "ABSORPTION_RECLAIM"].copy()

    rows: list[dict[str, Any]] = []
    for trade in absorption.itertuples(index=False):
        scenario_id = str(trade.scenario_id)
        contact = base._first_event(
            events,
            scenario_id,
            {"UPPER_POOL_SWEEP_RECLAIM", "LOWER_POOL_SWEEP_RECLAIM"},
        )
        confirmation_event = base._first_event(
            events,
            scenario_id,
            {"OPPOSITE_DISPLACEMENT_MSS"},
        )
        contact_ns = int(contact["event_time_ns"])
        confirmation_ns = int(confirmation_event["event_time_ns"])
        opened_ns = int(trade.opened_ns)
        confirmation_bars = completed_minutes_between(contact_ns, confirmation_ns)
        entry_bars = completed_minutes_between(confirmation_ns, opened_ns)
        if confirmation_bars % 5 != 0:
            raise RuntimeError(
                f"scenario {scenario_id} MSS is not on the five-minute clock"
            )
        if confirmation_bars not in {5, 10, 15}:
            raise RuntimeError(
                f"scenario {scenario_id} confirmation horizon is outside the "
                f"declared three-bar state window: {confirmation_bars} minutes"
            )

        reason = str(contact["reason_code"])
        attack_orientation = 1 if reason.startswith("UPPER_") else -1
        reversal_orientation = -attack_orientation
        atr = float((confirmation_event.get("details") or {})["atr"])
        source_window = base.exact_completed_window(
            flow,
            start_exclusive_ns=contact_ns - NS_PER_FIVE_MINUTES,
            end_inclusive_ns=contact_ns,
            expected_bars=5,
        )
        confirmation_window = base.exact_completed_window(
            flow,
            start_exclusive_ns=contact_ns,
            end_inclusive_ns=confirmation_ns,
            expected_bars=confirmation_bars,
        )
        entry_window = base.exact_completed_window(
            flow,
            start_exclusive_ns=confirmation_ns,
            end_inclusive_ns=opened_ns,
            expected_bars=entry_bars,
        )
        source_summary = base.summarize_oriented_flow(
            source_window,
            orientation=attack_orientation,
            atr=atr,
        )
        confirmation_summary = base.summarize_oriented_flow(
            confirmation_window,
            orientation=reversal_orientation,
            atr=atr,
        )
        entry_summary = base.summarize_oriented_flow(
            entry_window,
            orientation=reversal_orientation,
            atr=atr,
        )
        rules = base.transfer_rules(
            source=source_summary,
            confirmation=confirmation_summary,
            entry=entry_summary,
        )
        source_directional = max(source_summary.directional_volume, 1e-12)
        confirmation_directional = max(
            confirmation_summary.directional_volume,
            1e-12,
        )
        rows.append(
            {
                "stage": str(args.stage),
                "scenario_id": scenario_id,
                "direction": str(trade.direction),
                "contact_reason": reason,
                "contact_time_ns": contact_ns,
                "confirmation_time_ns": confirmation_ns,
                "confirmation_bars": confirmation_bars,
                "entry_bars": entry_bars,
                "opened_ns": opened_ns,
                "closed_ns": int(trade.closed_ns),
                "net_pnl": float(trade.net_pnl),
                "net_return_on_nav": float(trade.net_return_on_nav),
                "win": float(trade.net_pnl) > 0.0,
                "expected_rr": float(trade.expected_rr),
                "penetration_atr": float(
                    (contact.get("details") or {}).get("penetration_atr", 0.0)
                ),
                "wick_fraction": float(
                    (contact.get("details") or {}).get("wick_fraction", 0.0)
                ),
                "volume_z": float(
                    (contact.get("details") or {}).get("volume_z", 0.0)
                ),
                "source_attack_imbalance": source_summary.oriented_imbalance,
                "source_attack_minute_fraction": (
                    source_summary.oriented_minute_fraction
                ),
                "source_attack_price_move_atr": (
                    source_summary.oriented_price_move_atr
                ),
                "source_attack_path_efficiency": (
                    source_summary.oriented_path_efficiency
                ),
                "confirmation_reversal_imbalance": (
                    confirmation_summary.oriented_imbalance
                ),
                "confirmation_reversal_minute_fraction": (
                    confirmation_summary.oriented_minute_fraction
                ),
                "confirmation_terminal_reversal_imbalance": (
                    confirmation_summary.terminal_oriented_imbalance
                ),
                "confirmation_reversal_price_move_atr": (
                    confirmation_summary.oriented_price_move_atr
                ),
                "confirmation_reversal_path_efficiency": (
                    confirmation_summary.oriented_path_efficiency
                ),
                "entry_reversal_imbalance": entry_summary.oriented_imbalance,
                "entry_terminal_reversal_imbalance": (
                    entry_summary.terminal_oriented_imbalance
                ),
                "confirmation_to_source_directional_volume": (
                    confirmation_summary.directional_volume / source_directional
                ),
                "confirmation_residual_attack_ratio": (
                    confirmation_summary.counter_volume / confirmation_directional
                ),
                **rules,
                "source_flow": asdict(source_summary),
                "confirmation_flow": asdict(confirmation_summary),
                "entry_flow": asdict(entry_summary),
            }
        )

    pd.DataFrame(rows).to_csv(output / "initiative_flow_features.csv", index=False)
    summary = {
        "candidate": "candidate-07-initiative-flow-transition-v2",
        "stage": str(args.stage),
        "period": {
            "start": args.start.isoformat(),
            "end_exclusive": args.end.isoformat(),
        },
        "source_run_root": str(run_root),
        "absorption_trades": len(rows),
        "wins": sum(bool(row["win"]) for row in rows),
        "losses": sum(not bool(row["win"]) for row in rows),
        "confirmation_horizons_minutes": sorted(
            {int(row["confirmation_bars"]) for row in rows}
        ),
        "feature_separation": _feature_summary(rows),
        "predeclared_rules": _rule_summary(rows),
        "causal_contract": {
            "source_window": (
                "five completed minutes ending at sweep/reclaim observation"
            ),
            "confirmation_window": (
                "all completed minutes from source observation through the "
                "actual one-to-three-bar MSS confirmation"
            ),
            "entry_window": (
                "completed minute(s) after MSS through actual market-order "
                "submission"
            ),
            "outcome_joined_after_feature_computation": True,
            "orders_or_pnl_created": False,
            "future_information_in_features": False,
        },
    }
    write_json_atomic(
        output / "initiative_flow_summary.json",
        base._jsonable(summary),
    )
    print(json.dumps(base._jsonable(summary), indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    candidate_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=candidate_dir / "config.json")
    parser.add_argument("--stage", required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(".research-data/candidate-07"),
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(diagnose(build_parser().parse_args()))
