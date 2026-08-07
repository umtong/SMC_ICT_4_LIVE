"""NautilusTrader runner for direct Session Raid Reversal V1."""

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
import run_day_liquidity_delivery_nautilus_v1 as day_base
from aggtrade_acceptance_risk_v2 import RiskCompleteAggTradeAcceptanceStrategy
from session_raid_reversal_signals_v1 import (
    SIGNAL_REVISION,
    build_session_raid_reversal_signals,
)


IMPLEMENTATION_REVISION = "SESSION_RAID_REVERSAL_V1"
EXECUTION_ADAPTER_REVISION = "SESSION_RAID_REVERSAL_EXECUTION_LABELS_V1"
_ACTIVE_DAY_CONFIG = None
_ORIGINAL_GLOBAL_SIGNAL_SUMMARY = base._global_signal_summary
_ORIGINAL_SUITE_SUMMARY = base._suite_summary


def _build_signals(**kwargs: Any):
    if _ACTIVE_DAY_CONFIG is None:
        raise RuntimeError("session-raid-reversal day config was not initialized")
    kwargs.pop("require_retest_contraction", None)
    return build_session_raid_reversal_signals(**kwargs, day_config=_ACTIVE_DAY_CONFIG)


class SessionRaidReversalExecutionStrategy(RiskCompleteAggTradeAcceptanceStrategy):
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
        intent["candidate_time_horizon"] = "INTRADAY_TENS_OF_MINUTES_TO_SIX_HOURS"
        intent["scalping_alpha_inputs"] = False


def _global_signal_summary(signals_by_time_ns):
    summary = _ORIGINAL_GLOBAL_SIGNAL_SUMMARY(signals_by_time_ns)
    materialized = [signal for items in signals_by_time_ns.values() for signal in items]
    summary.update(
        {
            "by_scenario_family": dict(
                sorted(Counter(signal.scenario_family for signal in materialized).items())
            ),
            "signal_implementation_revision": SIGNAL_REVISION,
            "day_trading_timeframe_contract": (
                "H4_DRAW_TO_OPPOSITE_COMPLETED_SOURCE_SESSION_LIQUIDITY_VIA_COMPLETED_15M_RAID_RECLAIM"
            ),
            "scalping_alpha_inputs": False,
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
        family_counts.update(dict(result.get("detector", {})).get("by_scenario_family", {}))
    summary.update(
        {
            "signal_implementation_revision": SIGNAL_REVISION,
            "execution_adapter_revision": EXECUTION_ADAPTER_REVISION,
            "by_scenario_family": dict(sorted(family_counts.items())),
            "candidate_time_horizon": "INTRADAY_TENS_OF_MINUTES_TO_SIX_HOURS",
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
    global _ACTIVE_DAY_CONFIG
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if payload.get("implementation_revision") != IMPLEMENTATION_REVISION:
        raise ValueError("unexpected session-raid-reversal revision")
    if float(payload["risk_fraction"]) != 0.03:
        raise ValueError("V1 fixes current-shared-NAV planned loss at three percent")
    if list(payload["assets"]) != ["BTCUSDT"]:
        raise ValueError("V1 is BTC-first and cannot optimize across assets")
    if bool(payload["scenario_contract"].get("scalping_alpha_inputs", True)):
        raise ValueError("V1 must remain day-trading logic")
    _ACTIVE_DAY_CONFIG = day_base._load_delivery_config(payload)

    base.build_acceptance_signals = _build_signals
    base.AggTradeAcceptanceStrategy = SessionRaidReversalExecutionStrategy
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
        default=HERE / "config_session_raid_reversal_btc_v1.json",
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
        default=Path.home() / ".cache" / "smc4" / "candidate-08-session-raid",
    )
    args = parser.parse_args()
    run_suite(
        config_path=args.config.resolve(),
        pattern_config_path=args.pattern_config.resolve(),
        suite=args.suite,
        output=args.output.resolve(),
        data_cache=args.data_cache.resolve(),
        reuse_first_dir=(args.reuse_first_dir.resolve() if args.reuse_first_dir else None),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
