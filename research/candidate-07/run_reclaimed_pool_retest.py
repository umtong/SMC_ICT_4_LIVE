#!/usr/bin/env python3
"""Frozen BTC Week-1 tournament for the reclaimed-pool retest candidate."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Any

import diagnose_aggtrade_volume_time as volume_time
import diagnose_impact_resilience_1s as impact
import diagnose_reclaimed_pool_retest as retest
from data_aggtrades_1s import load_aggtrade_1s_bundle
from detect_volume_time_events import detect
from diagnose_aggtrade_resilience_v2 import preconsume_before_event_window
from diagnose_failed_flow import aggregate_flow
from diagnose_impact_resilience_1s_v2 import attach_causal_context_gap_safe
from diagnose_session_handoff import _align_positioning
from smc_ict_4.manifest import write_json_atomic


def _payload(logic: object) -> dict[str, Any]:
    return {
        name: getattr(logic, name)
        for name in getattr(logic, "__dataclass_fields__")
    }


def run(args: argparse.Namespace) -> int:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    volume_logic = volume_time.VolumeTimeLogic()
    volume_logic.validate()
    scenario_logic = retest.ReclaimedPoolRetestLogic()
    scenario_logic.validate()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)

    bundle = load_aggtrade_1s_bundle(
        symbol=str(config["symbol"]),
        trade_start=args.start,
        trade_end=args.end,
        positioning_warmup_days=int(config["warmup_days"]),
        event_warmup_days=args.event_warmup_days,
        cache_root=args.data_root.resolve(),
        manifest_destination=output / "data_manifest.json",
    )
    minute = impact._minute_features(
        bundle.minute_positioning.frame,
        atr_period=impact.ImpactLogic().minute_atr_period,
    )
    five = aggregate_flow(bundle.minute_positioning.frame, 5, 36)
    five = _align_positioning(
        five,
        bundle.minute_positioning.metrics,
        oi_period=impact.ImpactLogic().oi_period,
        oi_impulse_rank=impact.ImpactLogic().oi_impulse_rank,
    )
    five["timestamp_ns"] = five["timestamp_ns"].astype("int64")
    seconds = bundle.seconds.copy()
    seconds["close_time_ns"] = seconds["timestamp_ns"].astype("int64")
    bars = attach_causal_context_gap_safe(
        seconds,
        minute,
        five,
        history_windows=impact.ImpactLogic().history_windows,
        flow_quantile=impact.ImpactLogic().flow_quantile,
    )
    event_start_ns = int(bars.iloc[0]["timestamp_ns"])
    five_all = impact._pool_confirmations(
        five,
        timeframe="5M",
        radius=impact.ImpactLogic().five_minute_pivot_radius,
    )
    five_pools, preconsumption = preconsume_before_event_window(
        five_all,
        minute,
        event_start_ns=event_start_ns,
    )

    detector = detect(
        bars,
        source_pools=five_pools,
        trade_start_ns=impact._utc_ns(args.start),
        trade_end_ns=impact._utc_ns(args.end),
        logic=volume_logic,
        # Prior controlled experiments showed OI release reduced opportunity
        # without improving direct-impact path quality.  The state remains in
        # every event for attribution but is not an entry requirement here.
        require_oi_release=False,
    )
    write_json_atomic(
        output / "detector.json",
        {
            "candidate": "candidate-07",
            "family": "pure_volume_time_event_detector",
            "logic": _payload(volume_logic),
            "loader_diagnostics": bundle.diagnostics,
            "preconsumption": preconsumption,
            "future_information": False,
            "orders_or_pnl": False,
            **detector,
        },
    )

    baseline = retest.diagnose(
        bars,
        detector_report=detector,
        max_hold_seconds=int(config["max_hold_minutes"]) * 60,
        logic=scenario_logic,
        require_flow_confirmation=True,
    )
    baseline_payload = {
        "candidate": "candidate-07",
        "stage": "week-1",
        "family": "reclaimed_pool_retest_to_value",
        "variant": "baseline_three_second_flow_rejection",
        "period": {
            "start": args.start.isoformat(),
            "end_exclusive": args.end.isoformat(),
        },
        "logic": _payload(scenario_logic),
        "detector_summary": detector["summary"],
        "loader_diagnostics": bundle.diagnostics,
        "future_information": False,
        "orders_or_pnl": False,
        **baseline,
    }
    write_json_atomic(output / "baseline.json", baseline_payload)

    selected: str | None = None
    variants = [
        {
            "variant": baseline_payload["variant"],
            "passed": bool(baseline["summary"]["diagnostic_gate"]["passed"]),
            "summary": baseline["summary"],
        }
    ]
    if variants[0]["passed"]:
        selected = "baseline_three_second_flow_rejection"

    # One controlled ablation after a clean logic failure: keep the first
    # retest, reclaimed-side close, directional body, entry, stop, target and
    # slot contracts; remove only the directional aggressor-flow requirement.
    if selected is None:
        ablation = retest.diagnose(
            bars,
            detector_report=detector,
            max_hold_seconds=int(config["max_hold_minutes"]) * 60,
            logic=scenario_logic,
            require_flow_confirmation=False,
        )
        ablation_payload = {
            "candidate": "candidate-07",
            "stage": "week-1",
            "family": "reclaimed_pool_retest_to_value",
            "variant": "ablation_remove_retest_flow_only",
            "period": baseline_payload["period"],
            "logic": _payload(scenario_logic),
            "detector_summary": detector["summary"],
            "loader_diagnostics": bundle.diagnostics,
            "future_information": False,
            "orders_or_pnl": False,
            **ablation,
        }
        write_json_atomic(output / "ablation_no_flow.json", ablation_payload)
        passed = bool(ablation["summary"]["diagnostic_gate"]["passed"])
        variants.append(
            {
                "variant": ablation_payload["variant"],
                "passed": passed,
                "summary": ablation["summary"],
            }
        )
        if passed:
            selected = "ablation_remove_retest_flow_only"

    summary = {
        "candidate": "candidate-07",
        "stage": "week-1",
        "period": baseline_payload["period"],
        "source_commit_expected": args.source_commit,
        "implementation_clean": (
            int(bundle.diagnostics.get("out_of_order_rows", -1)) == 0
            and int(bundle.diagnostics.get("duplicate_agg_trade_ids", -1)) == 0
            and int(bundle.diagnostics.get("noncontiguous_second_transitions", -1)) == 0
            and int(bundle.diagnostics.get("second_rows", 0)) > 0
        ),
        "detector_summary": detector["summary"],
        "selected_structural_route": selected,
        "eligible_for_nautilus_implementation": selected is not None,
        "variants": variants,
        "interpretation": (
            "STRUCTURAL_ROUTE_SELECTED"
            if selected is not None
            else "ALL_STRUCTURAL_VARIANTS_FAILED"
        ),
    }
    write_json_atomic(output / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    candidate_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=candidate_dir / "config.json")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(".research-data/candidate-07"),
    )
    parser.add_argument("--event-warmup-days", type=int, default=1)
    parser.add_argument("--source-commit", default=None)
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
