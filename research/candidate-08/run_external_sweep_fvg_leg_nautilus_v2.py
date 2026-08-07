"""Run external-sweep displacement/retrace leg states through native NautilusTrader."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

import run_quote_resiliency_nautilus as base
from external_sweep_fvg_leg_signals_v2 import (
    SCENARIO_FAMILY,
    SIGNAL_REVISION,
    ExternalSweepFvgLegConfig,
    build_external_sweep_fvg_leg_signals,
)


RUNNER_REVISION = "EXTERNAL_SWEEP_FVG_LEG_NATIVE_NAUTILUS_ADAPTER_V2"
UNCLASSIFIED_FAMILY = "UNCLASSIFIED_EXTERNAL_SWEEP_FVG_LEG_SCENARIO"
DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent / "config_external_sweep_fvg_leg_btc_v2.json"
)
DEFAULT_PATTERN_CONFIG = Path(__file__).resolve().parent / "config_range_fvg.json"


def _signal_config() -> ExternalSweepFvgLegConfig:
    payload = base._load_payload()
    revision = str(payload.get("signal_implementation_revision", ""))
    if revision != SIGNAL_REVISION:
        raise RuntimeError(
            f"external-sweep leg signal/config mismatch: {revision!r} != {SIGNAL_REVISION!r}"
        )
    config = ExternalSweepFvgLegConfig.from_mapping(
        dict(payload["external_sweep_fvg_leg_config"])
    )
    config.validate()
    return config


def _signal_family(signal: Any) -> str:
    value = getattr(signal, "scenario_family", None)
    if value:
        return str(value)
    details = getattr(signal, "details", {})
    if isinstance(details, Mapping) and details.get("scenario_family"):
        return str(details["scenario_family"])
    return UNCLASSIFIED_FAMILY


def _build_signals(**kwargs: Any):
    kwargs.pop("require_retest_contraction", None)
    return build_external_sweep_fvg_leg_signals(
        **kwargs,
        config=_signal_config(),
    )


def _global_signal_summary(
    signals_by_time_ns: Mapping[int, tuple[Any, ...]],
) -> dict[str, Any]:
    base.execution.runner.FAMILY_MODE = "both"
    summary = base._ORIGINAL_GLOBAL_SIGNAL_SUMMARY(signals_by_time_ns)
    summary.update(
        {
            "implementation_revision": SIGNAL_REVISION,
            "runner_revision": RUNNER_REVISION,
            "feature_revision": base.FEATURE_REVISION,
            "data_revision": base.DATA_REVISION,
            "execution_adapter_revision": base.EXECUTION_ADAPTER_REVISION,
            "native_quote_revision": base.NATIVE_QUOTE_REVISION,
            "risk_accounting_revision": base.RISK_ACCOUNTING_REVISION,
            "scenario_family": SCENARIO_FAMILY,
            "ten_second_cadence_contract": "EXACT_CONSECUTIVE_COMPLETED_10_SECONDS",
        }
    )
    return summary


def _suite_summary(
    config: Mapping[str, Any],
    suite: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    base.execution.runner.FAMILY_MODE = "both"
    summary = base._ORIGINAL_SUITE_SUMMARY(config, suite, results)
    summary.update(
        {
            "implementation_revision": SIGNAL_REVISION,
            "runner_revision": RUNNER_REVISION,
            "feature_revision": base.FEATURE_REVISION,
            "data_revision": base.DATA_REVISION,
            "execution_adapter_revision": base.EXECUTION_ADAPTER_REVISION,
            "native_quote_revision": base.NATIVE_QUOTE_REVISION,
            "risk_accounting_revision": base.RISK_ACCOUNTING_REVISION,
            "trade_path_diagnostic_revision": base.DIAGNOSTIC_REVISION,
            "scenario_family": SCENARIO_FAMILY,
            "scenario_contract": (
                "COMPLETED_EXTERNAL_SWEEP_THEN_MULTI_MINUTE_INTERNAL_MSS_FVG_"
                "DISPLACEMENT_LEG_THEN_LOWER_OPPOSING_ENERGY_FVG_RETRACE_LEG_"
                "THEN_SEPARATE_REACCELERATION_WITH_NATIVE_L1_ENTRY"
            ),
            "ablation": "none",
            "diagnostic_only_ablation": False,
            "ten_second_cadence_contract": "EXACT_CONSECUTIVE_COMPLETED_10_SECONDS",
        }
    )
    closed_trades = [
        trade
        for result in results
        for trade in result.get("closed_trade_records", [])
    ]
    path_summary = base.summarize_trade_path_diagnostics(closed_trades)
    closed_count = int(summary.get("closed_trades", 0))
    revision_counts = Counter(
        str(trade.get("path_diagnostic", {}).get("diagnostic_revision"))
        for trade in closed_trades
    )
    if closed_count == 0:
        revision_counts = Counter({base.DIAGNOSTIC_REVISION: 0})
    path_complete = (
        int(path_summary.get("records", -1)) == closed_count
        and int(path_summary.get("complete_records", -1)) == closed_count
        and revision_counts == Counter({base.DIAGNOSTIC_REVISION: closed_count})
    )
    path_summary["diagnostic_revision_counts"] = dict(sorted(revision_counts.items()))
    path_summary["expected_diagnostic_revision"] = base.DIAGNOSTIC_REVISION
    summary["trade_path_diagnostic_summary"] = path_summary
    checks = summary.setdefault("suite_gate_checks", {})
    checks["single_external_sweep_leg_family_enabled"] = True
    checks["complete_post_run_trade_path_diagnostics"] = path_complete
    summary["suite_gate_passed"] = bool(
        summary.get("suite_gate_passed", False) and path_complete
    )
    return summary


base.execution.runner.INITIATIVE_FAMILY = SCENARIO_FAMILY
base.execution.runner.FAILED_AUCTION_FAMILY = "UNUSED_EXTERNAL_SWEEP_LEG_FAMILY"
base.execution.runner.UNCLASSIFIED_FAMILY = UNCLASSIFIED_FAMILY
base.execution.runner.FAMILY_MODE = "both"
base.execution.runner.FAMILY_MODES = {
    "both": frozenset((SCENARIO_FAMILY,)),
}
base.execution.runner.build_auction_router_signals = build_external_sweep_fvg_leg_signals
base.execution.runner._build_router_signals = _build_signals
base.execution.runner._signal_family = _signal_family
base._build_signals = _build_signals
base._global_signal_summary = _global_signal_summary
base._suite_summary = _suite_summary
base.execution.runner.base_runner.build_acceptance_signals = _build_signals
base.execution.runner.base_runner._global_signal_summary = _global_signal_summary
base.execution.runner.base_runner._suite_summary = _suite_summary


def run_suite(
    *,
    config_path: Path,
    pattern_config_path: Path,
    suite: str,
    output: Path,
    data_cache: Path,
    reuse_first_dir: Path | None = None,
) -> dict[str, Any]:
    return base.run_suite(
        config_path=config_path,
        pattern_config_path=pattern_config_path,
        suite=suite,
        output=output,
        data_cache=data_cache,
        reuse_first_dir=reuse_first_dir,
        ablation=base.BASE_ABLATION,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("first", "screen"), default="first")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--pattern-config", type=Path, default=DEFAULT_PATTERN_CONFIG)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reuse-first-dir", type=Path, default=None)
    parser.add_argument(
        "--data-cache",
        type=Path,
        default=Path.home() / ".cache" / "smc4" / "candidate-08-external-sweep-fvg-leg",
    )
    args = parser.parse_args()
    run_suite(
        config_path=args.config.resolve(),
        pattern_config_path=args.pattern_config.resolve(),
        suite=args.suite,
        output=args.output.resolve(),
        data_cache=args.data_cache.resolve(),
        reuse_first_dir=(
            args.reuse_first_dir.resolve()
            if args.reuse_first_dir is not None
            else None
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
