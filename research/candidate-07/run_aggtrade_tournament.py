#!/usr/bin/env python3
"""Run the frozen Week-1 aggregate-trade structural tournament once.

The checksum-verified data bundle is loaded and reduced a single time. Candidate
families are evaluated in the mandated order:

1. fixed fifteen-second impact-resilience baseline;
2. remove only the OI-release requirement after a clean logic failure;
3. aggressor-volume-time impact-asymmetry baseline;
4. remove only its OI-release requirement after a clean logic failure;
5. retarget accepted volume-time events to the pre-attack 15-second VWAP;
6. remove only volume weighting and use the same bucket's close after failure;
7. only if prior routes fail, require impact -> one-minute MSS, ranked
   displacement, causal FVG and first episode-bounded FVG retest;
8. remove only the FVG-retest requirement after a clean logic failure.

No family here creates orders, fills, PnL, cash or NAV. A structural pass only
selects a route for immediate NautilusTrader implementation on the same Week-1.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Any

# Applies exact-second, target-timing, event-exclusivity and nullable-context
# implementation corrections before the shared diagnostic modules are used.
import run_aggtrade_resilience_second_safe as safety  # noqa: F401

import diagnose_aggtrade_resilience as fixed
import diagnose_aggtrade_volume_time as volume_time
import diagnose_impact_mss_fvg_paths as mss_fvg_paths
import diagnose_impact_resilience_1s as impact
import diagnose_pre_attack_value as pre_attack_value
from data_aggtrades_1s import load_aggtrade_1s_bundle
from diagnose_aggtrade_resilience_v2 import preconsume_before_event_window
from diagnose_failed_flow import aggregate_flow
from diagnose_impact_resilience_1s_v2 import attach_causal_context_gap_safe
from diagnose_session_handoff import _align_positioning
from model_impact_mss_fvg import ImpactMSSFVGLogic
from run_aggtrade_resilience_second_safe import (
    deduplicate_contact_pools_event_safe,
)
from smc_ict_4.manifest import write_json_atomic


def _implementation_clean(diagnostics: dict[str, int]) -> bool:
    return (
        int(diagnostics.get("out_of_order_rows", -1)) == 0
        and int(diagnostics.get("duplicate_agg_trade_ids", -1)) == 0
        and int(diagnostics.get("second_rows", 0)) > 0
        and int(diagnostics.get("raw_rows", 0)) > 0
    )


def _gate_passed(report: dict[str, Any]) -> bool:
    return bool(report["summary"]["diagnostic_gate"]["passed"])


def _write_variant(
    destination: Path,
    *,
    family: str,
    variant: str,
    period: dict[str, str],
    logic: dict[str, Any],
    loader_diagnostics: dict[str, int],
    preconsumption: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "candidate": "candidate-07",
        "stage": "week-1",
        "family": family,
        "variant": variant,
        "period": period,
        "logic": logic,
        "loader_diagnostics": loader_diagnostics,
        "preconsumption": preconsumption,
        "future_information": False,
        "orders_or_pnl": False,
        **result,
    }
    write_json_atomic(destination, payload)
    return payload


def _logic_payload(logic: object) -> dict[str, Any]:
    fields = getattr(logic, "__dataclass_fields__")
    return {name: getattr(logic, name) for name in fields}


def run(args: argparse.Namespace) -> int:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    fixed_logic = impact.ImpactLogic()
    fixed_logic.validate()
    volume_logic = volume_time.VolumeTimeLogic()
    volume_logic.validate()
    pre_attack_vwap_logic = pre_attack_value.PreAttackValueLogic(
        target_statistic="vwap",
    )
    pre_attack_vwap_logic.validate()
    pre_attack_close_logic = pre_attack_value.PreAttackValueLogic(
        target_statistic="close",
    )
    pre_attack_close_logic.validate()
    mss_fvg_logic = ImpactMSSFVGLogic()
    mss_fvg_logic.validate()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    positioning_warmup = int(config["warmup_days"])
    if not 1 <= args.event_warmup_days <= positioning_warmup:
        raise ValueError("event_warmup_days must be in [1, config warmup_days]")
    bundle = load_aggtrade_1s_bundle(
        symbol=str(config["symbol"]),
        trade_start=args.start,
        trade_end=args.end,
        positioning_warmup_days=positioning_warmup,
        event_warmup_days=args.event_warmup_days,
        cache_root=args.data_root.resolve(),
        manifest_destination=output_dir / "aggtrade_tournament_data_manifest.json",
    )
    minute = impact._minute_features(
        bundle.minute_positioning.frame,
        atr_period=fixed_logic.minute_atr_period,
    )
    five = aggregate_flow(bundle.minute_positioning.frame, 5, 36)
    five = _align_positioning(
        five,
        bundle.minute_positioning.metrics,
        oi_period=fixed_logic.oi_period,
        oi_impulse_rank=fixed_logic.oi_impulse_rank,
    )
    five["timestamp_ns"] = five["timestamp_ns"].astype("int64")
    event_seconds = bundle.seconds.copy()
    event_seconds["close_time_ns"] = event_seconds["timestamp_ns"].astype("int64")
    bars = attach_causal_context_gap_safe(
        event_seconds,
        minute,
        five,
        history_windows=fixed_logic.history_windows,
        flow_quantile=fixed_logic.flow_quantile,
    )
    event_start_ns = int(bars.iloc[0]["timestamp_ns"])

    one_all = impact._pool_confirmations(
        minute,
        timeframe="1M",
        radius=fixed_logic.one_minute_pivot_radius,
    )
    five_all = impact._pool_confirmations(
        five,
        timeframe="5M",
        radius=fixed_logic.five_minute_pivot_radius,
    )
    one_pools, one_pre = preconsume_before_event_window(
        one_all,
        minute,
        event_start_ns=event_start_ns,
    )
    five_pools, five_pre = preconsume_before_event_window(
        five_all,
        minute,
        event_start_ns=event_start_ns,
    )
    fixed_sources, fixed_collision = deduplicate_contact_pools_event_safe(
        bars,
        five_pools,
    )
    targets = {"1M": one_pools, "5M": five_pools}
    period = {
        "start": args.start.isoformat(),
        "end_exclusive": args.end.isoformat(),
    }
    preconsumption = {
        "one_minute": one_pre,
        "five_minute": five_pre,
        "fixed_contact_collision": fixed_collision,
    }
    implementation_clean = _implementation_clean(bundle.diagnostics)
    records: list[dict[str, Any]] = []
    selected: str | None = None

    fixed_baseline_result = fixed.diagnose(
        bars,
        source_pools=fixed_sources,
        target_pools=targets,
        trade_start_ns=impact._utc_ns(args.start),
        trade_end_ns=impact._utc_ns(args.end),
        max_hold_seconds=int(config["max_hold_minutes"]) * 60,
        logic=fixed_logic,
        require_oi_release=True,
    )
    fixed_baseline = _write_variant(
        output_dir / "fixed_time_baseline.json",
        family="fixed_time_impact_resilience",
        variant="baseline_oi_release",
        period=period,
        logic=_logic_payload(fixed_logic),
        loader_diagnostics=bundle.diagnostics,
        preconsumption=preconsumption,
        result=fixed_baseline_result,
    )
    records.append(fixed_baseline)
    if _gate_passed(fixed_baseline):
        selected = "fixed_time_baseline"

    fixed_ablation: dict[str, Any] | None = None
    if selected is None and implementation_clean:
        fixed_ablation_result = fixed.diagnose(
            bars,
            source_pools=fixed_sources,
            target_pools=targets,
            trade_start_ns=impact._utc_ns(args.start),
            trade_end_ns=impact._utc_ns(args.end),
            max_hold_seconds=int(config["max_hold_minutes"]) * 60,
            logic=fixed_logic,
            require_oi_release=False,
        )
        fixed_ablation = _write_variant(
            output_dir / "fixed_time_ablation_no_oi.json",
            family="fixed_time_impact_resilience",
            variant="ablation_remove_oi_release_only",
            period=period,
            logic=_logic_payload(fixed_logic),
            loader_diagnostics=bundle.diagnostics,
            preconsumption=preconsumption,
            result=fixed_ablation_result,
        )
        records.append(fixed_ablation)
        if _gate_passed(fixed_ablation):
            selected = "fixed_time_ablation_no_oi"

    volume_baseline: dict[str, Any] | None = None
    volume_ablation: dict[str, Any] | None = None
    if selected is None and implementation_clean:
        volume_baseline_result = volume_time.diagnose(
            bars,
            source_pools=five_pools,
            target_pools=targets,
            trade_start_ns=impact._utc_ns(args.start),
            trade_end_ns=impact._utc_ns(args.end),
            max_hold_seconds=int(config["max_hold_minutes"]) * 60,
            logic=volume_logic,
            require_oi_release=True,
        )
        volume_baseline = _write_variant(
            output_dir / "volume_time_baseline.json",
            family="volume_time_impact_resilience",
            variant="baseline_oi_release",
            period=period,
            logic=_logic_payload(volume_logic),
            loader_diagnostics=bundle.diagnostics,
            preconsumption={"one_minute": one_pre, "five_minute": five_pre},
            result=volume_baseline_result,
        )
        records.append(volume_baseline)
        if _gate_passed(volume_baseline):
            selected = "volume_time_baseline"

    if selected is None and implementation_clean and volume_baseline is not None:
        volume_ablation_result = volume_time.diagnose(
            bars,
            source_pools=five_pools,
            target_pools=targets,
            trade_start_ns=impact._utc_ns(args.start),
            trade_end_ns=impact._utc_ns(args.end),
            max_hold_seconds=int(config["max_hold_minutes"]) * 60,
            logic=volume_logic,
            require_oi_release=False,
        )
        volume_ablation = _write_variant(
            output_dir / "volume_time_ablation_no_oi.json",
            family="volume_time_impact_resilience",
            variant="ablation_remove_oi_release_only",
            period=period,
            logic=_logic_payload(volume_logic),
            loader_diagnostics=bundle.diagnostics,
            preconsumption={"one_minute": one_pre, "five_minute": five_pre},
            result=volume_ablation_result,
        )
        records.append(volume_ablation)
        if _gate_passed(volume_ablation):
            selected = "volume_time_ablation_no_oi"

    # Structural redesign after the direct-impact target failure: preserve the
    # accepted volume-time event, but target the completed auction value from
    # which the failed attack originated rather than a remote liquidity pool.
    pre_attack_vwap: dict[str, Any] | None = None
    if selected is None and implementation_clean and volume_ablation is not None:
        pre_attack_vwap_result = pre_attack_value.diagnose(
            bars,
            upstream_report=volume_ablation_result,
            max_hold_seconds=int(config["max_hold_minutes"]) * 60,
            logic=pre_attack_vwap_logic,
        )
        pre_attack_vwap = _write_variant(
            output_dir / "pre_attack_value_vwap.json",
            family="pre_attack_auction_value",
            variant="baseline_prior_15s_vwap",
            period=period,
            logic=_logic_payload(pre_attack_vwap_logic),
            loader_diagnostics=bundle.diagnostics,
            preconsumption=preconsumption,
            result=pre_attack_vwap_result,
        )
        records.append(pre_attack_vwap)
        if _gate_passed(pre_attack_vwap):
            selected = "pre_attack_value_vwap"

    # One controlled ablation: remove only volume weighting from the already
    # completed pre-attack bucket and use its final trade price.
    if (
        selected is None
        and implementation_clean
        and pre_attack_vwap is not None
        and volume_ablation is not None
    ):
        pre_attack_close_result = pre_attack_value.diagnose(
            bars,
            upstream_report=volume_ablation_result,
            max_hold_seconds=int(config["max_hold_minutes"]) * 60,
            logic=pre_attack_close_logic,
        )
        pre_attack_close = _write_variant(
            output_dir / "pre_attack_value_ablation_close.json",
            family="pre_attack_auction_value",
            variant="ablation_remove_volume_weighting",
            period=period,
            logic=_logic_payload(pre_attack_close_logic),
            loader_diagnostics=bundle.diagnostics,
            preconsumption=preconsumption,
            result=pre_attack_close_result,
        )
        records.append(pre_attack_close)
        if _gate_passed(pre_attack_close):
            selected = "pre_attack_value_ablation_close"

    # Independent successor: preserve the OI-qualified impact event, but wait
    # for the ICT-style MSS/displacement/FVG sequence and first valid retest.
    mss_fvg_baseline: dict[str, Any] | None = None
    if selected is None and implementation_clean:
        mss_fvg_baseline_result = mss_fvg_paths.evaluate(
            minute,
            bars,
            upstream_scenarios=fixed_baseline_result["scenarios"],
            target_pools=targets,
            source_stop_buffer_atr=fixed_logic.stop_buffer_atr,
            maximum_hold_seconds=int(config["max_hold_minutes"]) * 60,
            logic=mss_fvg_logic,
            minimum_rr=fixed_logic.minimum_rr,
            require_fvg_retest=True,
        )
        mss_fvg_baseline = _write_variant(
            output_dir / "impact_mss_fvg_baseline.json",
            family="impact_mss_fvg_retest",
            variant="baseline_first_fvg_retest",
            period=period,
            logic=_logic_payload(mss_fvg_logic),
            loader_diagnostics=bundle.diagnostics,
            preconsumption=preconsumption,
            result=mss_fvg_baseline_result,
        )
        records.append(mss_fvg_baseline)
        if _gate_passed(mss_fvg_baseline):
            selected = "impact_mss_fvg_baseline"

    # One controlled ablation only: keep impact, OI, MSS, ranked displacement,
    # FVG, stop and target contracts; remove only the requirement to wait for
    # the FVG first-retest and measure entry at the completed MSS close.
    if selected is None and implementation_clean and mss_fvg_baseline is not None:
        mss_fvg_ablation_result = mss_fvg_paths.evaluate(
            minute,
            bars,
            upstream_scenarios=fixed_baseline_result["scenarios"],
            target_pools=targets,
            source_stop_buffer_atr=fixed_logic.stop_buffer_atr,
            maximum_hold_seconds=int(config["max_hold_minutes"]) * 60,
            logic=mss_fvg_logic,
            minimum_rr=fixed_logic.minimum_rr,
            require_fvg_retest=False,
        )
        mss_fvg_ablation = _write_variant(
            output_dir / "impact_mss_fvg_ablation_no_retest.json",
            family="impact_mss_fvg_retest",
            variant="ablation_remove_fvg_retest_only",
            period=period,
            logic=_logic_payload(mss_fvg_logic),
            loader_diagnostics=bundle.diagnostics,
            preconsumption=preconsumption,
            result=mss_fvg_ablation_result,
        )
        records.append(mss_fvg_ablation)
        if _gate_passed(mss_fvg_ablation):
            selected = "impact_mss_fvg_ablation_no_retest"

    compact = [
        {
            "family": item["family"],
            "variant": item["variant"],
            "passed": _gate_passed(item),
            "summary": item["summary"],
        }
        for item in records
    ]
    tournament = {
        "candidate": "candidate-07",
        "stage": "week-1",
        "source_commit_expected": args.source_commit,
        "period": period,
        "implementation_clean": implementation_clean,
        "loader_diagnostics": bundle.diagnostics,
        "preconsumption": preconsumption,
        "selected_structural_route": selected,
        "eligible_for_nautilus_implementation": selected is not None,
        "variants": compact,
        "interpretation": (
            "STRUCTURAL_ROUTE_SELECTED"
            if selected is not None
            else "IMPLEMENTATION_ERROR"
            if not implementation_clean
            else "ALL_STRUCTURAL_VARIANTS_FAILED"
        ),
    }
    write_json_atomic(output_dir / "tournament_summary.json", tournament)
    print(json.dumps(tournament, indent=2, sort_keys=True))
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
