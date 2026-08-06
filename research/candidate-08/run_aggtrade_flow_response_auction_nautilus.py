"""Run the causal flow-response auction detector through the verified native Nautilus engine.

Only the detector and economic-family vocabulary are replaced.  The shared-margin account,
current-NAV 3% risk sizing, market OUO bracket, fees, slippage reserve, official funding and mark
prices, liquidation, global one-order/position limit, evidence generation, and contract checks all
remain owned by the already-verified candidate-08 Nautilus runner.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

import run_aggtrade_auction_router_nautilus as runner
from aggtrade_flow_response_auction_signals import (
    ABSORPTION_FAMILY,
    IMPLEMENTATION_REVISION,
    INITIATIVE_FAMILY,
    build_flow_response_auction_signals,
)


FAMILY_MODES = {
    "both": frozenset((INITIATIVE_FAMILY, ABSORPTION_FAMILY)),
    "initiative_only": frozenset((INITIATIVE_FAMILY,)),
    "absorption_only": frozenset((ABSORPTION_FAMILY,)),
}


def _active_family_mode() -> str:
    mode = os.environ.get("FLOW_RESPONSE_AUCTION_FAMILY_MODE", "both").strip()
    if mode not in FAMILY_MODES:
        raise RuntimeError(f"unsupported flow-response family mode: {mode!r}")
    return mode


# Rebind only detector-facing globals.  All execution-facing base-runner functions remain the
# verified native implementation.
runner.INITIATIVE_FAMILY = INITIATIVE_FAMILY
runner.FAILED_AUCTION_FAMILY = ABSORPTION_FAMILY
runner.FAMILY_MODES = FAMILY_MODES
runner._active_family_mode = _active_family_mode
runner.build_auction_router_signals = build_flow_response_auction_signals

_original_suite_summary = runner._auction_suite_summary


def _flow_response_suite_summary(
    config: Mapping[str, Any],
    suite: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = _original_suite_summary(config, suite, results)
    mode = _active_family_mode()
    summary["implementation_revision"] = IMPLEMENTATION_REVISION
    summary["flow_response_family_mode"] = mode
    summary["scenario_contract"] = "CAUSAL_AGGRESSIVE_FLOW_PRICE_RESPONSE_AT_COMPLETED_EXTERNAL_LIQUIDITY"
    return summary


runner._auction_suite_summary = _flow_response_suite_summary
runner.base_runner.build_acceptance_signals = runner._build_router_signals
runner.base_runner._suite_summary = _flow_response_suite_summary


if __name__ == "__main__":
    raise SystemExit(runner.base_runner.main())
