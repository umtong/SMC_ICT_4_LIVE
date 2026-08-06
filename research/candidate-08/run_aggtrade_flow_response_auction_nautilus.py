"""Route flow-response auction signals through the verified native Nautilus runner.

Only detector selection, family filtering, and reporting vocabulary are replaced. The shared-margin
account, current-NAV 3% loss-budget sizing, OUO orders, fees, causal slippage reserve, official
funding and mark prices, liquidation, and the global one-order/position constraint remain in the
verified candidate-08 base runner.
"""

from __future__ import annotations

from collections import Counter
import os
from typing import Any, Mapping

import run_aggtrade_auction_router_nautilus as runner
from aggtrade_acceptance_signals import AcceptanceSignalBundle
from aggtrade_flow_response_auction_signals_v2 import (
    ABSORPTION_FAMILY,
    IMPLEMENTATION_REVISION,
    INITIATIVE_FAMILY,
    build_flow_response_auction_signals,
)


UNCLASSIFIED_FAMILY = "UNCLASSIFIED_FLOW_RESPONSE_SCENARIO"
FAMILY_MODES = {
    "both": frozenset((INITIATIVE_FAMILY, ABSORPTION_FAMILY)),
    "initiative_only": frozenset((INITIATIVE_FAMILY,)),
    "absorption_only": frozenset((ABSORPTION_FAMILY,)),
}


def _active_family_mode() -> str:
    mode = os.environ.get("FLOW_RESPONSE_AUCTION_FAMILY_MODE", "both").strip()
    if mode not in FAMILY_MODES:
        raise RuntimeError(
            f"unsupported FLOW_RESPONSE_AUCTION_FAMILY_MODE={mode!r}; "
            f"expected one of {sorted(FAMILY_MODES)}"
        )
    return mode


def _signal_family(signal: Any) -> str:
    details = getattr(signal, "details", {})
    if isinstance(details, Mapping):
        value = details.get("scenario_family")
        if value:
            return str(value)
    return UNCLASSIFIED_FAMILY


def _filter_bundle(bundle: AcceptanceSignalBundle) -> AcceptanceSignalBundle:
    """Apply the one permitted diagnostic family mode without changing base detection."""

    mode = _active_family_mode()
    if mode == "both":
        return bundle
    allowed = FAMILY_MODES[mode]
    retained: dict[int, tuple[Any, ...]] = {}
    removed: list[dict[str, Any]] = []
    removed_by_family: Counter[str] = Counter()
    for timestamp_ns, signals in bundle.signals_by_time_ns.items():
        kept: list[Any] = []
        for signal in signals:
            family = _signal_family(signal)
            if family in allowed:
                kept.append(signal)
            else:
                removed_by_family[family] += 1
                removed.append(
                    {
                        "scenario_id": str(signal.scenario_id),
                        "symbol": str(signal.symbol),
                        "boundary_id": str(signal.boundary_id),
                        "signal_time_ns": int(signal.signal_time_ns),
                        "reason": "DIAGNOSTIC_FAMILY_MODE_REMOVED",
                        "removed_family": family,
                        "auction_family_mode": mode,
                    }
                )
        if kept:
            retained[int(timestamp_ns)] = tuple(kept)

    diagnostics = dict(bundle.diagnostics)
    diagnostics["FAMILY_MODE_REMOVED_SIGNALS"] = len(removed)
    diagnostics["FAMILY_MODE_RETAINED_SIGNALS"] = sum(
        len(signals) for signals in retained.values()
    )
    for family, count in removed_by_family.items():
        diagnostics[f"FAMILY_MODE_REMOVED_{family}"] = int(count)
    return AcceptanceSignalBundle(
        signals_by_time_ns=retained,
        diagnostics=dict(sorted(diagnostics.items())),
        rejected_scenarios=tuple(bundle.rejected_scenarios) + tuple(removed),
    )


def _build_flow_response_signals(**kwargs: Any) -> AcceptanceSignalBundle:
    return _filter_bundle(build_flow_response_auction_signals(**kwargs))


runner.INITIATIVE_FAMILY = INITIATIVE_FAMILY
runner.FAILED_AUCTION_FAMILY = ABSORPTION_FAMILY
runner.UNCLASSIFIED_FAMILY = UNCLASSIFIED_FAMILY
runner.FAMILY_MODES = FAMILY_MODES
runner.build_auction_router_signals = build_flow_response_auction_signals
runner._build_router_signals = _build_flow_response_signals
runner._signal_family = _signal_family
runner._filter_bundle = _filter_bundle

_original_global_signal_summary = runner._auction_global_signal_summary
_original_suite_summary = runner._auction_suite_summary


def _flow_response_global_signal_summary(
    signals_by_time_ns: Mapping[int, tuple[Any, ...]],
) -> dict[str, Any]:
    runner.FAMILY_MODE = _active_family_mode()
    summary = _original_global_signal_summary(signals_by_time_ns)
    summary["flow_response_family_mode"] = runner.FAMILY_MODE
    summary["implementation_revision"] = IMPLEMENTATION_REVISION
    return summary


def _flow_response_suite_summary(
    config: Mapping[str, Any],
    suite: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    mode = _active_family_mode()
    runner.FAMILY_MODE = mode
    summary = _original_suite_summary(config, suite, results)
    summary["implementation_revision"] = IMPLEMENTATION_REVISION
    summary["flow_response_family_mode"] = mode
    summary["auction_family_mode"] = mode
    summary["scenario_contract"] = (
        "CAUSAL_AGGRESSIVE_FLOW_PRICE_RESPONSE_AT_COMPLETED_EXTERNAL_LIQUIDITY"
    )
    base_mode = mode == "both"
    summary["diagnostic_family_ablation"] = not base_mode
    summary["promotable"] = bool(summary.get("promotable", True) and base_mode)
    checks = summary.setdefault("suite_gate_checks", {})
    checks["base_contract_includes_both_flow_response_families"] = base_mode
    summary["suite_gate_passed"] = bool(
        summary.get("suite_gate_passed", False) and base_mode
    )
    return summary


runner.base_runner.build_acceptance_signals = _build_flow_response_signals
runner.base_runner._global_signal_summary = _flow_response_global_signal_summary
runner.base_runner._suite_summary = _flow_response_suite_summary


if __name__ == "__main__":
    raise SystemExit(runner.base_runner.main())
