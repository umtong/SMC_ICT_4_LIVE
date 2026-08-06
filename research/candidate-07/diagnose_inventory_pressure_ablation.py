#!/usr/bin/env python3
"""Ablate the post-contact hold from inventory-pressure continuation.

Changed variable only: the separate completed five-minute outside-hold stage is
removed.  An otherwise identical OI-release/attack-flow contact may enter at the
contact close only when that completed contact bar itself closes outside with a
directional body.  Contact pools, OI contact requirement, one-slot blocking,
structural stop, internal-to-external target ladder and exit-safe accounting are
unchanged.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from data_positioning import load_positioning_bundle
from diagnose_failed_flow import aggregate_flow
from diagnose_inventory_handoff import _directional_body, _same_direction_flow
from diagnose_inventory_handoff_exit_safe import _exit_safe_path_result
from diagnose_inventory_pressure_continuation import (
    PressureLogic,
    _bar_payload,
    _contacted_external,
    _consume_crossed,
    _copy_pool,
    _target,
    _utc_ns,
    five_minute_pool_confirmations,
)
from diagnose_mtf_liquidity import Pool, context_bars, pool_confirmations
from diagnose_session_handoff import _align_positioning
from smc_ict_4.manifest import write_json_atomic


def diagnose(
    bars: pd.DataFrame,
    *,
    contact_confirmations: Mapping[int, list[Pool]],
    internal_confirmations: Mapping[int, list[Pool]],
    trade_start_ns: int,
    trade_end_ns: int,
    logic: PressureLogic,
) -> dict[str, Any]:
    external_contacts: dict[str, Pool] = {}
    internal_targets: dict[str, Pool] = {}
    external_targets: dict[str, Pool] = {}
    scenarios: list[dict[str, Any]] = []
    contact_counts: Counter[str] = Counter()
    block_until = -1

    for index in range(1, len(bars.index)):
        row = bars.loc[index]
        timestamp_ns = int(row["timestamp_ns"])
        for pool in contact_confirmations.get(timestamp_ns, []):
            external_contacts[pool.pool_id] = pool
            copied = _copy_pool(pool, "T15")
            external_targets[copied.pool_id] = copied
        for pool in internal_confirmations.get(timestamp_ns, []):
            copied = _copy_pool(pool, "T5")
            internal_targets[copied.pool_id] = copied

        previous_close = float(bars.loc[index - 1]["close"])
        _consume_crossed(internal_targets, row, previous_close, timestamp_ns)
        _consume_crossed(external_targets, row, previous_close, timestamp_ns)
        atr_value = row["atr"]
        atr = float(atr_value) if not pd.isna(atr_value) else 0.0
        upper, lower = _contacted_external(
            external_contacts,
            row,
            previous_close,
            minimum_penetration=logic.contact_min_atr * atr if atr > 0.0 else 0.0,
        )

        if index <= block_until:
            for pool in [*upper, *lower]:
                pool.consumed = True
                pool.consumed_ts_ns = timestamp_ns
            continue
        if upper and lower:
            for pool in [*upper, *lower]:
                pool.consumed = True
                pool.consumed_ts_ns = timestamp_ns
            contact_counts["AMBIGUOUS_BOTH_SIDES"] += 1
            continue
        touched = upper or lower
        if not touched:
            continue
        pool = touched[0]
        for crossed in touched:
            crossed.consumed = True
            crossed.consumed_ts_ns = timestamp_ns

        if not trade_start_ns <= timestamp_ns < trade_end_ns:
            contact_counts["OUTSIDE_TRADE_INTERVAL"] += 1
            continue
        if atr <= 0.0:
            contact_counts["NO_ATR"] += 1
            continue
        if not bool(row["positioning_valid"]):
            contact_counts["POSITIONING_INVALID"] += 1
            continue
        if str(row["inventory_state"]) != "RELEASE":
            contact_counts[f"CONTACT_{str(row['inventory_state'])}"] += 1
            continue
        penetration = (
            (float(row["high"]) - pool.level) / atr
            if pool.side == "UPPER"
            else (pool.level - float(row["low"])) / atr
        )
        if not logic.contact_min_atr <= penetration <= logic.contact_max_atr:
            contact_counts["PENETRATION_OUTSIDE_BOUNDS"] += 1
            continue
        direction = "LONG" if pool.side == "UPPER" else "SHORT"
        if not (
            _same_direction_flow(row, direction, logic.attack_imbalance)
            and float(row["flow_z"]) >= logic.flow_z
        ):
            contact_counts["RELEASE_WITHOUT_ATTACK_FLOW"] += 1
            continue

        contact_counts["PRESSURE_CONTACT"] += 1
        outside = (
            float(row["close"]) > pool.level + logic.outside_buffer_atr * atr
            if direction == "LONG"
            else float(row["close"]) < pool.level - logic.outside_buffer_atr * atr
        )
        body_ok = abs(float(row["close"]) - float(row["open"])) >= (
            logic.confirmation_body_atr * atr
        )
        if not (outside and body_ok and _directional_body(row, direction)):
            scenarios.append(
                {
                    "scenario_id": f"c07ipca-{timestamp_ns}-{pool.pool_id}",
                    "outcome": "CONTACT_NOT_ACCEPTED",
                    "direction": direction,
                    "pool_id": pool.pool_id,
                    "contact": _bar_payload(row),
                }
            )
            continue

        entry = float(row["close"])
        if direction == "LONG":
            stop = min(float(row["low"]), float(pool.level)) - logic.stop_buffer_atr * atr
            risk = entry - stop
        else:
            stop = max(float(row["high"]), float(pool.level)) + logic.stop_buffer_atr * atr
            risk = stop - entry
        base = {
            "scenario_id": f"c07ipca-{timestamp_ns}-{pool.pool_id}",
            "route": "CONTACT_CLOSE_PRESSURE_CONTINUATION_ABLATION",
            "direction": direction,
            "pool_id": pool.pool_id,
            "pool_side": pool.side,
            "liquidity_level": float(pool.level),
            "contact": _bar_payload(row),
            "entry": entry,
            "stop": stop,
            "risk": risk,
            "risk_atr": risk / atr,
        }
        if risk <= 0.0:
            scenarios.append({**base, "outcome": "NONPOSITIVE_RISK"})
            continue
        selected = _target(
            internal_targets,
            external_targets,
            direction=direction,
            entry=entry,
            risk=risk,
            minimum_rr=logic.minimum_rr,
        )
        if selected is None:
            scenarios.append({**base, "outcome": "NO_CAUSAL_LIQUIDITY_TARGET_AT_MINIMUM_RR"})
            continue
        target_class, target_pool_id, target, expected_rr = selected
        path, block_until = _exit_safe_path_result(
            bars,
            start_index=index,
            direction=direction,
            entry=entry,
            stop=stop,
            target=target,
            max_hold_bars=logic.max_hold_bars,
        )
        scenarios.append(
            {
                **base,
                "outcome": "ENTRY_READY",
                "target_class": target_class,
                "target_pool_id": target_pool_id,
                "target": target,
                "expected_rr": expected_rr,
                "path": path,
            }
        )

    entry = [item for item in scenarios if item.get("outcome") == "ENTRY_READY"]
    outcomes = Counter(str((item.get("path") or {}).get("outcome")) for item in entry)
    dates = {str(item["contact"]["timestamp"])[:10] for item in entry}
    targets = Counter(str(item.get("target_class")) for item in entry)
    mfe = [float(item["path"]["mfe_r"]) for item in entry if item["path"].get("mfe_r") is not None]
    mae = [float(item["path"]["mae_r"]) for item in entry if item["path"].get("mae_r") is not None]
    gate = {
        "minimum_entry_ready": len(entry) >= 7,
        "minimum_active_days": len(dates) >= 4,
        "more_targets_than_stops": outcomes["TARGET"] > outcomes["STOP"],
        "median_mfe_at_least_minimum_rr": bool(mfe) and float(pd.Series(mfe).median()) >= logic.minimum_rr,
        "median_mae_below_one_r": bool(mae) and float(pd.Series(mae).median()) < 1.0,
    }
    gate["passed"] = all(gate.values())
    return {
        "summary": {
            "contact_counts": dict(sorted(contact_counts.items())),
            "scenarios": len(scenarios),
            "entry_ready": len(entry),
            "active_days": len(dates),
            "path_outcome_counts": dict(sorted(outcomes.items())),
            "target_minus_stop": outcomes["TARGET"] - outcomes["STOP"],
            "target_class_counts": dict(sorted(targets.items())),
            "median_mfe_r": float(pd.Series(mfe).median()) if mfe else None,
            "median_mae_r": float(pd.Series(mae).median()) if mae else None,
            "diagnostic_gate": gate,
        },
        "scenarios": scenarios,
    }


def run(args: argparse.Namespace) -> int:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    bundle = load_positioning_bundle(
        symbol=str(config["symbol"]),
        trade_start=args.start,
        trade_end=args.end,
        warmup_days=int(config["warmup_days"]),
        cache_root=args.data_root.resolve(),
        manifest_destination=output.with_name("inventory_pressure_ablation_data_manifest.json"),
    )
    flow_logic = dict(config["flow_logic"])
    bars = aggregate_flow(bundle.frame, int(flow_logic["signal_minutes"]), int(flow_logic["flow_period"]))
    logic = PressureLogic()
    aligned = _align_positioning(bars, bundle.metrics, oi_period=logic.oi_period, oi_impulse_rank=logic.oi_impulse_rank)
    result = diagnose(
        aligned,
        contact_confirmations=pool_confirmations(context_bars(bundle.frame)),
        internal_confirmations=five_minute_pool_confirmations(aligned, radius=logic.internal_pivot_radius),
        trade_start_ns=_utc_ns(args.start),
        trade_end_ns=_utc_ns(args.end),
        logic=logic,
    )
    payload = {
        "candidate": "candidate-07",
        "stage": args.stage,
        "ablation": "REMOVE_SEPARATE_POST_CONTACT_OUTSIDE_HOLD",
        "controlled_variables": {
            "data": "identical checksum-verified public archives",
            "contact_pool": "identical confirmed 15-minute swing pool",
            "contact_OI_and_flow": "identical release impulse and attack flow",
            "stop": "same auction-structure family",
            "target_hierarchy": "identical 5m internal then 15m external pools",
            "changed_variable": "separate post-contact hold only",
            "orders_or_pnl": False,
        },
        **result,
    }
    write_json_atomic(output, payload)
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    candidate_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=candidate_dir / "config.json")
    parser.add_argument("--stage", default="week-1-ablation")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=Path(".research-data/candidate-07"))
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
