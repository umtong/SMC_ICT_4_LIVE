"""Route delayed boundary reacceptance through the verified native Nautilus runner.

Only detector selection, the one predeclared initial-state ablation, evidence vocabulary and the
variable-length causal event serializer are replaced.  The verified native base continues to own
the shared margin account, current-NAV 3% loss-budget sizing, market OUO bracket, fees, causal stop
reserve, official funding and mark prices, liquidation, global one-order/position constraint, and
all order/fill/account reports.
"""

from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
from typing import Any, Mapping

from aggtrade_delayed_reacceptance_signals_v3 import (
    ABLATION_INITIAL_MODE,
    BASE_INITIAL_MODE,
    DelayedReacceptanceConfig,
    IMPLEMENTATION_REVISION,
    REACCEPTANCE_FAMILY,
    build_delayed_reacceptance_signals,
)
from aggtrade_flow_response import FlowResponseConfig
from flow_response_trade_path_diagnostics_v2 import (
    DIAGNOSTIC_REVISION,
    summarize_trade_path_diagnostics,
)
import run_aggtrade_flow_response_auction_nautilus as execution


UNUSED_FAMILY = "UNUSED_DELAYED_REACCEPTANCE_FAMILY"
UNCLASSIFIED_FAMILY = "UNCLASSIFIED_DELAYED_REACCEPTANCE_SCENARIO"
INITIAL_MODES = frozenset((BASE_INITIAL_MODE, ABLATION_INITIAL_MODE))
DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent / "config_delayed_reacceptance_btc_v1.json"
)


def _active_initial_mode() -> str:
    mode = os.environ.get("DELAYED_REACCEPTANCE_INITIAL_MODE", BASE_INITIAL_MODE).strip()
    if mode not in INITIAL_MODES:
        raise RuntimeError(
            f"unsupported DELAYED_REACCEPTANCE_INITIAL_MODE={mode!r}; "
            f"expected one of {sorted(INITIAL_MODES)}"
        )
    return mode


def _config_path() -> Path:
    raw = os.environ.get("DELAYED_REACCEPTANCE_CONFIG_PATH")
    return (Path(raw) if raw else DEFAULT_CONFIG_PATH).resolve()


def _load_reacceptance_config() -> DelayedReacceptanceConfig:
    payload = json.loads(_config_path().read_text(encoding="utf-8"))
    revision = str(payload.get("implementation_revision", ""))
    if revision != IMPLEMENTATION_REVISION:
        raise RuntimeError(
            f"delayed reacceptance implementation/config mismatch: "
            f"{revision!r} != {IMPLEMENTATION_REVISION!r}"
        )
    config = DelayedReacceptanceConfig(
        response=FlowResponseConfig(**dict(payload["flow_response_config"])),
        **dict(payload["delayed_reacceptance_config"]),
    )
    config.validate()
    return config


def _build_signals(**kwargs: Any):
    kwargs["reacceptance_config"] = _load_reacceptance_config()
    kwargs["initial_mode"] = _active_initial_mode()
    return build_delayed_reacceptance_signals(**kwargs)


def _write_merged_events(
    path: Path,
    *,
    signals_by_time_ns: Mapping[int, tuple[Any, ...]],
    execution_events: list[dict[str, Any]],
) -> int:
    """Write complete causal event chains without assuming every detector has three states."""

    base_runner = execution.runner.base_runner
    materialized: list[tuple[Any, int]] = []
    for signals in signals_by_time_ns.values():
        for signal in signals:
            if not signal.events:
                raise RuntimeError(
                    f"scenario {signal.scenario_id!r} emitted an empty logic-event chain"
                )
            for ordinal, event in enumerate(signal.events, start=1):
                materialized.append(
                    base_runner._event_to_research(event, ordinal * 10)
                )
    for raw in execution_events:
        reference = raw.get("reference_price")
        materialized.append(
            (
                base_runner.ResearchEvent(
                    scenario_id=str(raw["scenario_id"]),
                    instrument_id=str(raw["instrument_id"]),
                    event_type=str(raw["event_type"]),
                    event_time_ns=int(raw["event_time_ns"]),
                    observed_time_ns=int(raw["observed_time_ns"]),
                    previous_state=str(raw["previous_state"]),
                    next_state=str(raw["next_state"]),
                    reason_code=str(raw["reason_code"]),
                    reference_price=(
                        None
                        if reference is None
                        else format(float(reference), ".12g")
                    ),
                    details={
                        "symbol": raw.get("symbol"),
                        **dict(raw.get("details", {})),
                    },
                ),
                int(raw.get("sequence", 75)),
            )
        )
    materialized.sort(
        key=lambda item: (
            item[0].observed_time_ns,
            item[0].scenario_id,
            item[1],
            item[0].event_type,
        )
    )
    base_runner.write_events(path, [item[0] for item in materialized])
    return len(materialized)


execution.runner.INITIATIVE_FAMILY = REACCEPTANCE_FAMILY
execution.runner.FAILED_AUCTION_FAMILY = UNUSED_FAMILY
execution.runner.UNCLASSIFIED_FAMILY = UNCLASSIFIED_FAMILY
execution.runner.FAMILY_MODE = "both"
execution.runner.FAMILY_MODES = {
    "both": frozenset((REACCEPTANCE_FAMILY, UNUSED_FAMILY)),
}
execution.runner.build_auction_router_signals = build_delayed_reacceptance_signals
execution.runner._build_router_signals = _build_signals
execution.runner.base_runner.build_acceptance_signals = _build_signals
execution.runner.base_runner._write_merged_events = _write_merged_events

_ORIGINAL_GLOBAL_SIGNAL_SUMMARY = execution._original_global_signal_summary
_ORIGINAL_SUITE_SUMMARY = execution._original_suite_summary
_ORIGINAL_ENRICHED_CLOSED_TRADE_RECORDS = execution._flow_response_closed_trade_records


def _closed_trade_records(
    enriched_positions: list[dict[str, Any]],
    intents: list[dict[str, Any]],
    position_outcomes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return _ORIGINAL_ENRICHED_CLOSED_TRADE_RECORDS(
        enriched_positions,
        intents,
        position_outcomes,
    )


def _global_signal_summary(
    signals_by_time_ns: Mapping[int, tuple[Any, ...]],
) -> dict[str, Any]:
    execution.runner.FAMILY_MODE = "both"
    summary = _ORIGINAL_GLOBAL_SIGNAL_SUMMARY(signals_by_time_ns)
    summary["implementation_revision"] = IMPLEMENTATION_REVISION
    summary["delayed_reacceptance_initial_mode"] = _active_initial_mode()
    summary["ten_second_cadence_contract"] = "EXACT_CONSECUTIVE_10_SECONDS"
    summary["event_chain_contract"] = (
        "IDLE->INTERACTION_ARMED->INITIAL_OUTWARD_RESPONSE"
        "->BOUNDARY_RECLAIMED->CONFIRMED"
    )
    return summary


def _suite_summary(
    config: Mapping[str, Any],
    suite: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    execution.runner.FAMILY_MODE = "both"
    summary = _ORIGINAL_SUITE_SUMMARY(config, suite, results)
    mode = _active_initial_mode()
    base_mode = mode == BASE_INITIAL_MODE
    detector_signal_count = sum(
        int(result.get("detector", {}).get("signals", 0)) for result in results
    )
    detector_primary_count = sum(
        int(
            result.get("detector", {})
            .get("by_scenario_family", {})
            .get(REACCEPTANCE_FAMILY, 0)
        )
        for result in results
    )
    closed = [
        trade
        for result in results
        for trade in result.get("closed_trade_records", [])
    ]
    family_complete = (
        detector_signal_count == detector_primary_count
        and all(
            str(trade.get("scenario_family")) == REACCEPTANCE_FAMILY
            for trade in closed
        )
    )

    path_summary = summarize_trade_path_diagnostics(closed)
    closed_count = int(summary.get("closed_trades", 0))
    revision_counts = Counter(
        str(trade.get("path_diagnostic", {}).get("diagnostic_revision"))
        for trade in closed
    )
    if closed_count == 0:
        revision_counts = Counter({DIAGNOSTIC_REVISION: 0})
    path_complete = (
        int(path_summary.get("records", -1)) == closed_count
        and int(path_summary.get("complete_records", -1)) == closed_count
        and revision_counts == Counter({DIAGNOSTIC_REVISION: closed_count})
    )
    path_summary["diagnostic_revision_counts"] = dict(sorted(revision_counts.items()))
    path_summary["expected_diagnostic_revision"] = DIAGNOSTIC_REVISION

    summary["implementation_revision"] = IMPLEMENTATION_REVISION
    summary["delayed_reacceptance_initial_mode"] = mode
    summary["diagnostic_initial_ablation"] = not base_mode
    summary["promotable"] = bool(summary.get("promotable", True) and base_mode)
    summary["single_scenario_family"] = REACCEPTANCE_FAMILY
    summary["single_family_attribution_passed"] = family_complete
    summary["trade_path_diagnostic_revision"] = DIAGNOSTIC_REVISION
    summary["trade_path_diagnostic_summary"] = path_summary
    summary["ten_second_cadence_contract"] = "EXACT_CONSECUTIVE_10_SECONDS"
    summary["event_chain_contract"] = (
        "IDLE->INTERACTION_ARMED->INITIAL_OUTWARD_RESPONSE"
        "->BOUNDARY_RECLAIMED->CONFIRMED"
    )
    checks = summary.setdefault("suite_gate_checks", {})
    checks["single_delayed_reacceptance_family_attributed"] = family_complete
    checks["complete_post_run_trade_path_diagnostics"] = path_complete
    checks["base_initial_initiative_required"] = base_mode
    summary["suite_gate_passed"] = bool(
        summary.get("suite_gate_passed", False)
        and family_complete
        and path_complete
        and base_mode
    )
    return summary


execution.runner.base_runner._closed_trade_records = _closed_trade_records
execution.runner.base_runner._global_signal_summary = _global_signal_summary
execution.runner.base_runner._suite_summary = _suite_summary


if __name__ == "__main__":
    raise SystemExit(execution.runner.base_runner.main())
