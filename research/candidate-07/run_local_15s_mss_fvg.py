#!/usr/bin/env python3
"""Frozen BTC Week-1 local 15-second MSS/FVG structural tournament."""
from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import diagnose_aggtrade_volume_time as volume_time
import diagnose_impact_resilience_1s as impact
import diagnose_local_15s_mss_fvg as local_mss
from data_aggtrades_1s import load_aggtrade_1s_bundle
from detect_volume_time_events import detect
from diagnose_aggtrade_resilience_v2 import preconsume_before_event_window
from diagnose_failed_flow import aggregate_flow
from diagnose_impact_resilience_1s_v2 import attach_causal_context_gap_safe
from diagnose_session_handoff import _align_positioning
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from smc_ict_4.manifest import write_json_atomic


LOCAL_BAR_SECONDS = 15


def _payload(logic: object) -> dict[str, Any]:
    return {
        name: getattr(logic, name)
        for name in getattr(logic, "__dataclass_fields__")
    }


def _passed(result: dict[str, Any]) -> bool:
    return bool(result["summary"]["diagnostic_gate"]["passed"])


def run(args: argparse.Namespace) -> int:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    volume_logic = volume_time.VolumeTimeLogic()
    volume_logic.validate()
    mss_logic = local_mss.local_structure_logic(
        bar_seconds=LOCAL_BAR_SECONDS,
    )
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
    base_impact_logic = impact.ImpactLogic()
    minute = impact._minute_features(
        bundle.minute_positioning.frame,
        atr_period=base_impact_logic.minute_atr_period,
    )
    five = aggregate_flow(bundle.minute_positioning.frame, 5, 36)
    five = _align_positioning(
        five,
        bundle.minute_positioning.metrics,
        oi_period=base_impact_logic.oi_period,
        oi_impulse_rank=base_impact_logic.oi_impulse_rank,
    )
    five["timestamp_ns"] = five["timestamp_ns"].astype("int64")
    seconds = bundle.seconds.copy()
    seconds["close_time_ns"] = seconds["timestamp_ns"].astype("int64")
    bars = attach_causal_context_gap_safe(
        seconds,
        minute,
        five,
        history_windows=base_impact_logic.history_windows,
        flow_quantile=base_impact_logic.flow_quantile,
    )
    local_bars = local_mss.aggregate_complete_clock_bars(
        bars,
        bar_seconds=LOCAL_BAR_SECONDS,
    )
    event_start_ns = int(bars.iloc[0]["timestamp_ns"])
    five_all = impact._pool_confirmations(
        five,
        timeframe="5M",
        radius=base_impact_logic.five_minute_pivot_radius,
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
        require_oi_release=False,
    )
    detector_payload = {
        "candidate": "candidate-07",
        "family": "pure_volume_time_event_detector",
        "logic": _payload(volume_logic),
        "loader_diagnostics": bundle.diagnostics,
        "preconsumption": preconsumption,
        "future_information": False,
        "orders_or_pnl": False,
        **detector,
    }
    write_json_atomic(output / "detector.json", detector_payload)

    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    shared = {
        "local_bars": local_bars,
        "seconds": bars,
        "detector_report": detector,
        "target_pools": {"5M": five_pools},
        "maximum_hold_seconds": int(config["max_hold_minutes"]) * 60,
        "mss_logic": mss_logic,
        "price_increment": instrument.price_increment.as_decimal(),
        "taker_fee_rate": instrument.taker_fee or Decimal(0),
        "funding_reserve_bps": Decimal(
            str(config["risk_funding_reserve_bps"])
        ),
    }
    period = {
        "start": args.start.isoformat(),
        "end_exclusive": args.end.isoformat(),
    }
    execution_clock = {
        "bar_seconds": LOCAL_BAR_SECONDS,
        "swing_definition": "five completed local bars; radius two",
        "displacement_rank_history_seconds": (
            mss_logic.displacement_rank_period * LOCAL_BAR_SECONDS
        ),
        "maximum_mss_seconds": (
            mss_logic.maximum_mss_minutes * LOCAL_BAR_SECONDS
        ),
        "maximum_retest_seconds": (
            mss_logic.maximum_retest_minutes * LOCAL_BAR_SECONDS
        ),
        "atr_reference": "last causally available completed one-minute ATR",
    }
    target_contract = {
        "timeframe": "5M",
        "selection": (
            "nearest opposing pool confirmed and unconsumed before entry"
        ),
        "skip_nearer_unconsumed_pool_for_farther_pool": False,
        "empirical_minimum_rr": False,
        "cost_prerequisite": (
            "strictly positive after entry/target adverse ticks, taker fees "
            "and configured funding reserve"
        ),
    }

    baseline = local_mss.evaluate(
        **shared,
        require_fvg_retest=True,
    )
    baseline_payload = {
        "candidate": "candidate-07",
        "stage": "week-1",
        "family": "local_15s_mss_fvg_to_external_liquidity",
        "variant": "baseline_first_fvg_retest",
        "period": period,
        "mss_fvg_logic": _payload(mss_logic),
        "execution_clock": execution_clock,
        "target_contract": target_contract,
        "detector_summary": detector["summary"],
        "loader_diagnostics": bundle.diagnostics,
        "preconsumption": preconsumption,
        "future_information": False,
        "orders_or_pnl": False,
        **baseline,
    }
    write_json_atomic(output / "baseline.json", baseline_payload)

    baseline_passed = _passed(baseline)
    variants = [
        {
            "variant": baseline_payload["variant"],
            "passed": baseline_passed,
            "summary": baseline["summary"],
        }
    ]
    selected: str | None = (
        baseline_payload["variant"] if baseline_passed else None
    )

    # One controlled ablation after a clean baseline logic failure. Keep the
    # failed-auction event, local swing, displacement, FVG, source invalidation,
    # target hierarchy, costs and slot. Remove only the FVG first-retest wait.
    if selected is None:
        ablation = local_mss.evaluate(
            **shared,
            require_fvg_retest=False,
        )
        ablation_payload = {
            "candidate": "candidate-07",
            "stage": "week-1",
            "family": "local_15s_mss_fvg_to_external_liquidity",
            "variant": "ablation_remove_fvg_retest_only",
            "period": period,
            "mss_fvg_logic": _payload(mss_logic),
            "execution_clock": execution_clock,
            "target_contract": target_contract,
            "detector_summary": detector["summary"],
            "loader_diagnostics": bundle.diagnostics,
            "preconsumption": preconsumption,
            "future_information": False,
            "orders_or_pnl": False,
            **ablation,
        }
        write_json_atomic(
            output / "ablation_no_retest.json",
            ablation_payload,
        )
        ablation_passed = _passed(ablation)
        variants.append(
            {
                "variant": ablation_payload["variant"],
                "passed": ablation_passed,
                "summary": ablation["summary"],
            }
        )
        if ablation_passed:
            selected = ablation_payload["variant"]

    implementation_clean = (
        int(bundle.diagnostics.get("out_of_order_rows", -1)) == 0
        and int(bundle.diagnostics.get("duplicate_agg_trade_ids", -1)) == 0
        and int(
            bundle.diagnostics.get(
                "noncontiguous_second_transitions",
                -1,
            )
        )
        == 0
        and int(bundle.diagnostics.get("second_rows", 0)) > 0
        and not local_bars.empty
        and bool((local_bars["source_seconds"] == LOCAL_BAR_SECONDS).all())
    )
    summary = {
        "candidate": "candidate-07",
        "stage": "week-1",
        "period": period,
        "source_commit_expected": args.source_commit,
        "implementation_clean": implementation_clean,
        "detector_summary": detector["summary"],
        "execution_clock": execution_clock,
        "selected_structural_route": selected,
        "eligible_for_nautilus_implementation": (
            implementation_clean and selected is not None
        ),
        "variants": variants,
        "interpretation": (
            "STRUCTURAL_ROUTE_SELECTED"
            if implementation_clean and selected is not None
            else "IMPLEMENTATION_ERROR"
            if not implementation_clean
            else "BASELINE_AND_SINGLE_ABLATION_FAILED"
        ),
    }
    write_json_atomic(output / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    candidate_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=candidate_dir / "config.json",
    )
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
