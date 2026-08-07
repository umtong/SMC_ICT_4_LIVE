"""NautilusTrader runner for the BTC-first day-liquidity-delivery candidate.

This module only replaces the pure signal builder and truthful scenario labels. The pinned
candidate-08 shared-account NautilusTrader engine remains authoritative for data replay, one global
entry/position, current-NAV three-percent sizing, fees, causal stop slippage, funding, liquidation,
OUO orders, fills, positions, and NAV.
"""

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
from aggtrade_acceptance_risk_v2 import RiskCompleteAggTradeAcceptanceStrategy
from day_liquidity_delivery_context_v1 import DayLiquidityDeliveryConfig
from day_liquidity_delivery_signals_v1 import (
    SIGNAL_REVISION,
    build_day_liquidity_delivery_signals,
)


EXECUTION_ADAPTER_REVISION = "DAY_LIQUIDITY_DELIVERY_BAR_EXECUTION_LABELS_V1"
_ACTIVE_CONFIG: DayLiquidityDeliveryConfig | None = None
_ORIGINAL_GLOBAL_SIGNAL_SUMMARY = base._global_signal_summary
_ORIGINAL_SUITE_SUMMARY = base._suite_summary


def _load_delivery_config(payload: Mapping[str, Any]) -> DayLiquidityDeliveryConfig:
    section = dict(payload["day_liquidity_delivery_config"])
    config = DayLiquidityDeliveryConfig(
        h4_swing_span=int(section["h4_swing_span"]),
        h4_displacement_lookback=int(section["h4_displacement_lookback"]),
        h4_close_location=float(section["h4_close_location"]),
        h4_target_minimum_atr=float(section["h4_target_minimum_atr"]),
        session_boundary_excursion_atr=float(section["session_boundary_excursion_atr"]),
        acceptance_close_location=float(section["acceptance_close_location"]),
        five_swing_span=int(section["five_swing_span"]),
        five_displacement_lookback=int(section["five_displacement_lookback"]),
        five_close_location=float(section["five_close_location"]),
        structural_stop_buffer_atr=float(section["structural_stop_buffer_atr"]),
        maximum_delivery_minutes=int(section["maximum_delivery_minutes"]),
    )
    config.validate()
    return config


def _build_signals(**kwargs: Any):
    if _ACTIVE_CONFIG is None:
        raise RuntimeError("day-liquidity-delivery config was not initialized")
    kwargs.pop("require_retest_contraction", None)
    return build_day_liquidity_delivery_signals(**kwargs, config=_ACTIVE_CONFIG)


class DayLiquidityDeliveryExecutionStrategy(RiskCompleteAggTradeAcceptanceStrategy):
    """Preserve incumbent execution while correcting scenario-family evidence labels."""

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
        intent["scenario_family"] = str(signal.scenario_family)
        intent["signal_implementation_revision"] = SIGNAL_REVISION
        intent["execution_adapter_revision"] = EXECUTION_ADAPTER_REVISION
        intent["boundary_source"] = str(signal.boundary_source)
        intent["target_source"] = str(signal.target_source)


def _global_signal_summary(signals_by_time_ns):
    summary = _ORIGINAL_GLOBAL_SIGNAL_SUMMARY(signals_by_time_ns)
    materialized = [
        signal
        for items in signals_by_time_ns.values()
        for signal in items
    ]
    summary.update(
        {
            "by_scenario_family": dict(
                sorted(Counter(signal.scenario_family for signal in materialized).items())
            ),
            "signal_implementation_revision": SIGNAL_REVISION,
            "day_trading_timeframe_contract": (
                "H4_DRAW_TO_COMPLETED_DAY_WEEK_TARGET_VIA_SESSION_ROUTE_AND_5M_DELIVERY"
            ),
        }
    )
    return summary


def _suite_summary(
    config: Mapping[str, Any],
    suite: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = _ORIGINAL_SUITE_SUMMARY(config, suite, results)
    family_counts: Counter[str] = Counter()
    for result in results:
        detector = dict(result.get("detector", {}))
        family_counts.update(detector.get("by_scenario_family", {}))
    summary.update(
        {
            "signal_implementation_revision": SIGNAL_REVISION,
            "execution_adapter_revision": EXECUTION_ADAPTER_REVISION,
            "by_scenario_family": dict(sorted(family_counts.items())),
            "candidate_time_horizon": "INTRADAY_TENS_OF_MINUTES_TO_HOURS",
            "scalping_alpha_inputs": False,
        }
    )
    return summary


def run_suite(
    *,
    config_path: Path,
    pattern_config_path: Path,
    suite: str,
    output: Path,
    data_cache: Path,
    reuse_first_dir: Path | None = None,
) -> dict[str, Any]:
    global _ACTIVE_CONFIG
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if payload.get("implementation_revision") != "DAY_LIQUIDITY_DELIVERY_ROUTER_V1":
        raise ValueError("unexpected day-liquidity-delivery implementation revision")
    if float(payload["risk_fraction"]) != 0.03:
        raise ValueError("V1 fixes current-shared-NAV planned loss at three percent")
    if list(payload["assets"]) != ["BTCUSDT"]:
        raise ValueError("BTC-first V1 must not optimize across assets")
    _ACTIVE_CONFIG = _load_delivery_config(payload)

    base.build_acceptance_signals = _build_signals
    base.AggTradeAcceptanceStrategy = DayLiquidityDeliveryExecutionStrategy
    base._global_signal_summary = _global_signal_summary
    base._suite_summary = _suite_summary
    return base.run_suite(
        config_path=config_path,
        pattern_config_path=pattern_config_path,
        suite=suite,
        output=output,
        data_cache=data_cache,
        reuse_first_dir=reuse_first_dir,
        ablation="none",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("first", "screen"), default="first")
    parser.add_argument(
        "--config",
        type=Path,
        default=HERE / "config_day_liquidity_delivery_btc_v1.json",
    )
    parser.add_argument(
        "--pattern-config",
        type=Path,
        default=HERE / "config_range_fvg.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reuse-first-dir", type=Path, default=None)
    parser.add_argument(
        "--data-cache",
        type=Path,
        default=Path.home() / ".cache" / "smc4" / "candidate-08-day-delivery",
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
