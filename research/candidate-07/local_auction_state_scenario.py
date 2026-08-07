"""Scenario adapter for the local rejection/acceptance auction-state router."""
from __future__ import annotations

import json
from typing import Any, Mapping

from event_signal_data import CausalTradeSignal
from local_auction_state_router import diagnose_router
from nautilus_trader.model.identifiers import InstrumentId
import run_local_liquidity_sweep_mss_retest as local


_BASE_DISCOVER = local.discover_structural_signals


def discover_auction_state(
    *,
    config: Mapping[str, Any],
    bundle: Any,
    start: Any,
    end: Any,
    require_retest: bool,
) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Use the established data/pool contract and replace only scenario routing."""
    original_diagnose = local.diagnose
    local.diagnose = diagnose_router
    try:
        bars, upstream, selected, contract = _BASE_DISCOVER(
            config=config,
            bundle=bundle,
            start=start,
            end=end,
            require_retest=require_retest,
        )
    finally:
        local.diagnose = original_diagnose
    contract.update(
        {
            "family": "local_15s_auction_state_router",
            "variant": "rejection_and_acceptance_retests",
            "branches": ["REJECTION_REVERSAL", "ACCEPTANCE_CONTINUATION"],
            "first_touch_state_partition": (
                "inside reclaim routes rejection; efficient outside close routes acceptance"
            ),
            "accepted_break_invalidation": "completed close back inside source pool",
            "accepted_break_entry": "later completed broken-source-level retest and hold",
            "source_pool_reuse": False,
            "selected_summary": selected["summary"],
            "orders_or_pnl_created_by_preprocessor": False,
            "future_information": False,
        }
    )
    return bars, upstream, selected, contract


def build_auction_state_signals(
    *,
    report: Mapping[str, Any],
    upstream_report: Mapping[str, Any],
    instrument_id: InstrumentId,
) -> list[CausalTradeSignal]:
    del upstream_report
    output: list[CausalTradeSignal] = []
    for item in report.get("scenarios", ()):
        if item.get("outcome") != "ENTRY_READY":
            continue
        observed_ns = int(item["observed_time_ns"])
        branch = str(item["branch"])
        details = {
            "structural_family": "local_15s_auction_state_router",
            "branch": branch,
            "sweep": item["sweep"],
            "mss": item["mss"],
            "retest": item["retest"],
            "target_pool": item["target_pool"],
            "require_retest": True,
        }
        serialized = json.dumps(details, sort_keys=True)
        lowered = serialized.lower()
        for forbidden in ("path", "mfe", "mae", "terminal", "realized_r"):
            if forbidden in lowered:
                raise RuntimeError(f"future-path field leaked into signal: {forbidden}")
        output.append(
            CausalTradeSignal(
                instrument_id=instrument_id,
                scenario_id=str(item["scenario_id"]),
                direction=str(item["direction"]),
                entry_reference=float(item["entry"]),
                stop_price=float(item["stop"]),
                target_price=float(item["target"]),
                expected_rr=float(item["expected_rr"]),
                source_pool_id=str(item["source_pool_id"]),
                signal_kind=(
                    "LOCAL_15S_REJECTION_MSS_RETEST"
                    if branch == "REJECTION_REVERSAL"
                    else "LOCAL_15S_ACCEPTED_BREAK_RETEST"
                ),
                details_json=serialized,
                observed_time_ns=observed_ns,
                ts_event=observed_ns + 1,
                ts_init=observed_ns + 1,
            )
        )
    output.sort(key=lambda item: (item.ts_event, item.scenario_id))
    if len({item.scenario_id for item in output}) != len(output):
        raise RuntimeError("duplicate local auction-state scenario identifiers")
    return output


__all__ = [
    "build_auction_state_signals",
    "discover_auction_state",
]
