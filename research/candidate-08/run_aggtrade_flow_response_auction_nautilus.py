"""Route flow-response auction signals through the verified native Nautilus runner.

Only detector selection, family filtering, reporting vocabulary, and post-run diagnostics are
replaced. The shared-margin account, current-NAV 3% loss-budget sizing, OUO orders, fees, causal
slippage reserve, official funding and mark prices, liquidation, and the global one-order/position
constraint remain in the verified candidate-08 base runner.

Post-run path diagnostics capture the same exact-cadence ten-second frame already loaded for
Nautilus replay and attach structural stop/target path facts only after positions are closed. They
cannot affect signal selection, order submission, sizing or fills.
"""

from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
from typing import Any, Mapping

import run_aggtrade_auction_router_nautilus as runner
from aggtrade_acceptance_signals import AcceptanceSignalBundle
from aggtrade_flow_response import FlowResponseConfig
from aggtrade_flow_response_auction_signals_v3 import (
    ABSORPTION_FAMILY,
    IMPLEMENTATION_REVISION,
    INITIATIVE_FAMILY,
    FlowResponseAuctionConfig,
    build_flow_response_auction_signals,
)
from flow_response_trade_path_diagnostics_v2 import (
    DIAGNOSTIC_REVISION,
    enrich_closed_trade_records,
    summarize_trade_path_diagnostics,
)


UNCLASSIFIED_FAMILY = "UNCLASSIFIED_FLOW_RESPONSE_SCENARIO"
FAMILY_MODES = {
    "both": frozenset((INITIATIVE_FAMILY, ABSORPTION_FAMILY)),
    "initiative_only": frozenset((INITIATIVE_FAMILY,)),
    "absorption_only": frozenset((ABSORPTION_FAMILY,)),
}
DEFAULT_CONTRACT_CONFIG = (
    Path(__file__).resolve().parent / "config_flow_response_auction_btc_v1.json"
)

_CAPTURED_TEN_SECOND_FRAMES: dict[str, Any] = {}
_CURRENT_MAXIMUM_HOLD_MINUTES: int | None = None


def _active_family_mode() -> str:
    mode = os.environ.get("FLOW_RESPONSE_AUCTION_FAMILY_MODE", "both").strip()
    if mode not in FAMILY_MODES:
        raise RuntimeError(
            f"unsupported FLOW_RESPONSE_AUCTION_FAMILY_MODE={mode!r}; "
            f"expected one of {sorted(FAMILY_MODES)}"
        )
    return mode


def _contract_config_path() -> Path:
    value = os.environ.get("FLOW_RESPONSE_AUCTION_CONFIG_PATH")
    return (Path(value) if value else DEFAULT_CONTRACT_CONFIG).resolve()


def _load_auction_config() -> FlowResponseAuctionConfig:
    path = _contract_config_path()
    payload = json.loads(path.read_text(encoding="utf-8"))
    revision = str(payload.get("implementation_revision", ""))
    if revision != IMPLEMENTATION_REVISION:
        raise RuntimeError(
            f"flow-response implementation/config revision mismatch: {revision!r} "
            f"!= {IMPLEMENTATION_REVISION!r}"
        )
    response = FlowResponseConfig(**dict(payload["flow_response_config"]))
    auction = FlowResponseAuctionConfig(
        response=response,
        **dict(payload["flow_response_auction_config"]),
    )
    auction.validate()
    return auction


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
                        "implementation_revision": IMPLEMENTATION_REVISION,
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
    kwargs["auction_config"] = _load_auction_config()
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
_original_closed_trade_records = runner._auction_closed_trade_records
_original_load_ten_second_aggtrades = runner.base_runner.load_ten_second_aggtrades
_original_run_window = runner.base_runner.run_window


def _capturing_load_ten_second_aggtrades(*args: Any, **kwargs: Any):
    """Capture the exact official replay frame while preserving the loader result."""

    result = _original_load_ten_second_aggtrades(*args, **kwargs)
    symbol = kwargs.get("symbol")
    if symbol is None and args:
        symbol = args[0]
    if symbol is None:
        raise RuntimeError("ten-second loader call exposed no symbol for path diagnostics")
    frame, _sources, quality = result
    if frame.empty:
        raise RuntimeError(f"official aggregate-trade frame was empty for {symbol}")
    if int(quality.get("gap_count_over_11_seconds", -1)) != 0:
        raise RuntimeError(
            f"official aggregate-trade frame had a ten-second gap for {symbol}: {quality}"
        )
    _CAPTURED_TEN_SECOND_FRAMES[str(symbol)] = frame
    return result


def _flow_response_closed_trade_records(
    enriched_positions: list[dict[str, Any]],
    intents: list[dict[str, Any]],
    position_outcomes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    records = _original_closed_trade_records(
        enriched_positions,
        intents,
        position_outcomes,
    )
    if _CURRENT_MAXIMUM_HOLD_MINUTES is None:
        raise RuntimeError("path diagnostics had no active maximum-hold contract")
    return enrich_closed_trade_records(
        records=records,
        intents=intents,
        frames_by_symbol=_CAPTURED_TEN_SECOND_FRAMES,
        maximum_hold_minutes=_CURRENT_MAXIMUM_HOLD_MINUTES,
    )


def _flow_response_run_window(*args: Any, **kwargs: Any):
    """Scope captured replay frames to exactly one native run-window call."""

    global _CURRENT_MAXIMUM_HOLD_MINUTES
    config = kwargs.get("config")
    if config is None:
        raise RuntimeError("flow-response run window requires keyword config")
    _CAPTURED_TEN_SECOND_FRAMES.clear()
    _CURRENT_MAXIMUM_HOLD_MINUTES = int(config["maximum_hold_minutes"])
    try:
        return _original_run_window(*args, **kwargs)
    finally:
        _CAPTURED_TEN_SECOND_FRAMES.clear()
        _CURRENT_MAXIMUM_HOLD_MINUTES = None


def _flow_response_global_signal_summary(
    signals_by_time_ns: Mapping[int, tuple[Any, ...]],
) -> dict[str, Any]:
    runner.FAMILY_MODE = _active_family_mode()
    summary = _original_global_signal_summary(signals_by_time_ns)
    summary["flow_response_family_mode"] = runner.FAMILY_MODE
    summary["implementation_revision"] = IMPLEMENTATION_REVISION
    summary["ten_second_cadence_contract"] = "EXACT_CONSECUTIVE_10_SECONDS"
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
    summary["ten_second_cadence_contract"] = "EXACT_CONSECUTIVE_10_SECONDS"
    summary["trade_path_diagnostic_revision"] = DIAGNOSTIC_REVISION
    base_mode = mode == "both"
    summary["diagnostic_family_ablation"] = not base_mode
    summary["promotable"] = bool(summary.get("promotable", True) and base_mode)
    closed_trades = [
        trade
        for result in results
        for trade in result.get("closed_trade_records", [])
    ]
    path_summary = summarize_trade_path_diagnostics(closed_trades)
    path_revisions = Counter(
        str(trade.get("path_diagnostic", {}).get("diagnostic_revision"))
        for trade in closed_trades
    )
    closed_count = int(summary.get("closed_trades", 0))
    path_complete = (
        int(path_summary["records"]) == closed_count
        and int(path_summary["complete_records"]) == closed_count
        and path_revisions == Counter({DIAGNOSTIC_REVISION: closed_count})
    )
    path_summary["diagnostic_revision_counts"] = dict(sorted(path_revisions.items()))
    path_summary["expected_diagnostic_revision"] = DIAGNOSTIC_REVISION
    summary["trade_path_diagnostic_summary"] = path_summary
    checks = summary.setdefault("suite_gate_checks", {})
    checks["base_contract_includes_both_flow_response_families"] = base_mode
    checks["complete_post_run_trade_path_diagnostics"] = path_complete
    summary["suite_gate_passed"] = bool(
        summary.get("suite_gate_passed", False)
        and base_mode
        and path_complete
    )
    return summary


runner.base_runner.build_acceptance_signals = _build_flow_response_signals
runner.base_runner.load_ten_second_aggtrades = _capturing_load_ten_second_aggtrades
runner.base_runner._closed_trade_records = _flow_response_closed_trade_records
runner.base_runner.run_window = _flow_response_run_window
runner.base_runner._global_signal_summary = _flow_response_global_signal_summary
runner.base_runner._suite_summary = _flow_response_suite_summary


if __name__ == "__main__":
    raise SystemExit(runner.base_runner.main())
