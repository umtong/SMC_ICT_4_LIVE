"""Diagnostic-only NautilusTrader runner for the single no-standard-FVG ablation."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import run_aggtrade_acceptance_nautilus as base
import run_day_liquidity_delivery_nautilus_v1 as standard
from day_liquidity_delivery_no_fvg_ablation_v1 import (
    ABLATION_MODE,
    SIGNAL_REVISION,
    build_day_liquidity_delivery_no_fvg_ablation_signals,
)


EXECUTION_ADAPTER_REVISION = "DAY_LIQUIDITY_DELIVERY_NO_FVG_ABLATION_EXECUTION_V1"
_ACTIVE_CONFIG = None


def _build_signals(**kwargs: Any):
    if _ACTIVE_CONFIG is None:
        raise RuntimeError("day-liquidity-delivery ablation config was not initialized")
    kwargs.pop("require_retest_contraction", None)
    return build_day_liquidity_delivery_no_fvg_ablation_signals(
        **kwargs,
        config=_ACTIVE_CONFIG,
    )


class DiagnosticNoFVGExecutionStrategy(standard.DayLiquidityDeliveryExecutionStrategy):
    """Reuse the exact base execution and correct only diagnostic evidence labels."""

    def _submit_signal(
        self,
        signal: Any,
        geometry: dict[str, float | int],
        ts_event_ns: int,
    ) -> None:
        before = len(self.trade_intents)
        super()._submit_signal(signal, geometry, ts_event_ns)
        if len(self.trade_intents) <= before:
            return
        intent = self.trade_intents[-1]
        intent["signal_implementation_revision"] = SIGNAL_REVISION
        intent["execution_adapter_revision"] = EXECUTION_ADAPTER_REVISION
        intent["ablation_mode"] = ABLATION_MODE
        intent["diagnostic_only"] = True


def _global_signal_summary(signals_by_time_ns):
    summary = standard._ORIGINAL_GLOBAL_SIGNAL_SUMMARY(signals_by_time_ns)
    materialized = [signal for items in signals_by_time_ns.values() for signal in items]
    summary.update(
        {
            "by_scenario_family": dict(
                sorted(Counter(signal.scenario_family for signal in materialized).items())
            ),
            "signal_implementation_revision": SIGNAL_REVISION,
            "ablation_mode": ABLATION_MODE,
            "diagnostic_only": True,
            "day_trading_timeframe_contract": (
                "H4_DRAW_TO_COMPLETED_DAY_WEEK_TARGET_VIA_SESSION_ROUTE_AND_5M_DISPLACEMENT"
            ),
        }
    )
    return summary


def _suite_summary(
    config: Mapping[str, Any],
    suite: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = standard._ORIGINAL_SUITE_SUMMARY(config, suite, results)
    family_counts: Counter[str] = Counter()
    for result in results:
        detector = dict(result.get("detector", {}))
        family_counts.update(detector.get("by_scenario_family", {}))
    economic_gate = bool(summary.get("suite_gate_passed", False))
    economic_goal = bool(summary.get("goal_met", False))
    summary.update(
        {
            "candidate": "candidate-08-day-liquidity-delivery-no-fvg-ablation-btc-nautilus-v1",
            "signal_implementation_revision": SIGNAL_REVISION,
            "execution_adapter_revision": EXECUTION_ADAPTER_REVISION,
            "ablation_mode": ABLATION_MODE,
            "ablation_modes": [ABLATION_MODE],
            "by_scenario_family": dict(sorted(family_counts.items())),
            "candidate_time_horizon": "INTRADAY_TENS_OF_MINUTES_TO_HOURS",
            "scalping_alpha_inputs": False,
            "diagnostic_only": True,
            "economic_gate_before_diagnostic_block": economic_gate,
            "economic_goal_before_diagnostic_block": economic_goal,
            "direct_promotion_prohibited": True,
            "suite_gate_passed": False,
            "promotable": False,
            "goal_met": False,
        }
    )
    return summary


def run_suite(
    *,
    config_path: Path,
    pattern_config_path: Path,
    output: Path,
    data_cache: Path,
) -> dict[str, Any]:
    global _ACTIVE_CONFIG
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if payload.get("implementation_revision") != SIGNAL_REVISION:
        raise ValueError("unexpected no-FVG ablation implementation revision")
    if not bool(payload.get("diagnostic_only", False)):
        raise ValueError("the no-FVG result must remain diagnostic-only")
    if float(payload["risk_fraction"]) != 0.03:
        raise ValueError("diagnostic preserves current-shared-NAV three-percent risk")
    if list(payload["assets"]) != ["BTCUSDT"]:
        raise ValueError("diagnostic remains BTC-first and cannot optimize across assets")
    _ACTIVE_CONFIG = standard._load_delivery_config(payload)

    base.build_acceptance_signals = _build_signals
    base.AggTradeAcceptanceStrategy = DiagnosticNoFVGExecutionStrategy
    base._global_signal_summary = _global_signal_summary
    base._suite_summary = _suite_summary
    return base.run_suite(
        config_path=config_path,
        pattern_config_path=pattern_config_path,
        suite="first",
        output=output,
        data_cache=data_cache,
        reuse_first_dir=None,
        ablation="none",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=HERE / "config_day_liquidity_delivery_no_fvg_ablation_btc_v1.json",
    )
    parser.add_argument(
        "--pattern-config",
        type=Path,
        default=HERE / "config_range_fvg.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--data-cache",
        type=Path,
        default=Path.home() / ".cache" / "smc4" / "candidate-08-day-delivery",
    )
    args = parser.parse_args()
    run_suite(
        config_path=args.config.resolve(),
        pattern_config_path=args.pattern_config.resolve(),
        output=args.output.resolve(),
        data_cache=args.data_cache.resolve(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
