"""Route intrinsic repricing signals through the verified native Nautilus shared-account runner.

Only detector selection, path filtering and evidence vocabulary are replaced.  The verified native
runner continues to own the shared margin account, current-NAV 3% sizing, market OUO bracket, fees,
causal stop reserve, official funding and mark prices, liquidation, global one-order/position limit,
and all order/fill/account reports.

`INTRINSIC_REPRICING_PATH_MODE` defaults to `both_paths`.  Single-path modes exist only for the one
predeclared diagnostic after a valid base logic failure and are never directly promotable.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from aggtrade_acceptance_signals import AcceptanceSignalBundle
from aggtrade_flow_response import FlowResponseConfig
from aggtrade_intrinsic_repricing_signals import (
    DIRECT_PERSISTENCE_PATH,
    IMPLEMENTATION_REVISION,
    INTRINSIC_REPRICING_FAMILY,
    IntrinsicRepricingConfig,
    REPRICE_RESUMPTION_PATH,
    build_intrinsic_repricing_signals,
)
from flow_response_trade_path_diagnostics_v2 import (
    DIAGNOSTIC_REVISION,
    summarize_trade_path_diagnostics,
)
import run_aggtrade_flow_response_auction_nautilus as execution


UNUSED_FAMILY = "UNUSED_INTRINSIC_REPRICING_FAMILY"
UNCLASSIFIED_FAMILY = "UNCLASSIFIED_INTRINSIC_REPRICING_SCENARIO"
PATH_MODES = {
    "both_paths": frozenset((DIRECT_PERSISTENCE_PATH, REPRICE_RESUMPTION_PATH)),
    "direct_only": frozenset((DIRECT_PERSISTENCE_PATH,)),
    "reprice_only": frozenset((REPRICE_RESUMPTION_PATH,)),
}
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config_intrinsic_repricing_btc_v1.json"


def _active_path_mode() -> str:
    mode = os.environ.get("INTRINSIC_REPRICING_PATH_MODE", "both_paths").strip()
    if mode not in PATH_MODES:
        raise RuntimeError(
            f"unsupported INTRINSIC_REPRICING_PATH_MODE={mode!r}; "
            f"expected one of {sorted(PATH_MODES)}"
        )
    return mode


def _config_path() -> Path:
    raw = os.environ.get("INTRINSIC_REPRICING_CONFIG_PATH")
    return (Path(raw) if raw else DEFAULT_CONFIG_PATH).resolve()


def _load_repricing_config() -> IntrinsicRepricingConfig:
    payload = json.loads(_config_path().read_text(encoding="utf-8"))
    revision = str(payload.get("implementation_revision", ""))
    if revision != IMPLEMENTATION_REVISION:
        raise RuntimeError(
            f"intrinsic repricing implementation/config mismatch: "
            f"{revision!r} != {IMPLEMENTATION_REVISION!r}"
        )
    config = IntrinsicRepricingConfig(
        response=FlowResponseConfig(**dict(payload["flow_response_config"])),
        **dict(payload["intrinsic_repricing_config"]),
    )
    config.validate()
    return config


def _signal_path(signal: Any) -> str:
    details = getattr(signal, "details", {})
    if isinstance(details, Mapping):
        value = details.get("entry_path")
        if value:
            return str(value)
    return "UNCLASSIFIED_INTRINSIC_REPRICING_PATH"


def _filter_bundle(bundle: AcceptanceSignalBundle) -> AcceptanceSignalBundle:
    mode = _active_path_mode()
    if mode == "both_paths":
        return bundle
    allowed = PATH_MODES[mode]
    retained: dict[int, tuple[Any, ...]] = {}
    removed: list[dict[str, Any]] = []
    by_path: Counter[str] = Counter()
    for timestamp_ns, signals in bundle.signals_by_time_ns.items():
        kept: list[Any] = []
        for signal in signals:
            path = _signal_path(signal)
            if path in allowed:
                kept.append(signal)
            else:
                by_path[path] += 1
                removed.append(
                    {
                        "scenario_id": str(signal.scenario_id),
                        "symbol": str(signal.symbol),
                        "boundary_id": str(signal.boundary_id),
                        "signal_time_ns": int(signal.signal_time_ns),
                        "reason": "DIAGNOSTIC_ENTRY_PATH_REMOVED",
                        "removed_path": path,
                        "path_mode": mode,
                        "implementation_revision": IMPLEMENTATION_REVISION,
                    }
                )
        if kept:
            retained[int(timestamp_ns)] = tuple(kept)

    diagnostics = dict(bundle.diagnostics)
    diagnostics["DIAGNOSTIC_PATH_REMOVED_SIGNALS"] = len(removed)
    diagnostics["DIAGNOSTIC_PATH_RETAINED_SIGNALS"] = sum(
        len(values) for values in retained.values()
    )
    for path, count in by_path.items():
        diagnostics[f"DIAGNOSTIC_PATH_REMOVED_{path}"] = int(count)
    return AcceptanceSignalBundle(
        signals_by_time_ns=retained,
        diagnostics=dict(sorted(diagnostics.items())),
        rejected_scenarios=tuple(bundle.rejected_scenarios) + tuple(removed),
    )


def _build_signals(**kwargs: Any) -> AcceptanceSignalBundle:
    kwargs["repricing_config"] = _load_repricing_config()
    return _filter_bundle(build_intrinsic_repricing_signals(**kwargs))


# Rebind only detector-facing vocabulary and functions.
execution.runner.INITIATIVE_FAMILY = INTRINSIC_REPRICING_FAMILY
execution.runner.FAILED_AUCTION_FAMILY = UNUSED_FAMILY
execution.runner.UNCLASSIFIED_FAMILY = UNCLASSIFIED_FAMILY
execution.runner.FAMILY_MODE = "both"
execution.runner.FAMILY_MODES = {
    "both": frozenset((INTRINSIC_REPRICING_FAMILY, UNUSED_FAMILY)),
}
execution.runner.build_auction_router_signals = build_intrinsic_repricing_signals
execution.runner._build_router_signals = _build_signals
execution.runner._filter_bundle = _filter_bundle
execution.runner.base_runner.build_acceptance_signals = _build_signals

_ORIGINAL_GLOBAL_SIGNAL_SUMMARY = execution._original_global_signal_summary
_ORIGINAL_SUITE_SUMMARY = execution._original_suite_summary
_ORIGINAL_ENRICHED_CLOSED_TRADE_RECORDS = execution._flow_response_closed_trade_records


def _closed_trade_records(
    enriched_positions: list[dict[str, Any]],
    intents: list[dict[str, Any]],
    position_outcomes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    records = _ORIGINAL_ENRICHED_CLOSED_TRADE_RECORDS(
        enriched_positions,
        intents,
        position_outcomes,
    )
    path_by_scenario = {
        str(intent.get("scenario_id")): str(
            dict(intent.get("logic_details", {})).get(
                "entry_path",
                "UNCLASSIFIED_INTRINSIC_REPRICING_PATH",
            )
        )
        for intent in intents
    }
    for record in records:
        record["entry_path"] = path_by_scenario.get(
            str(record.get("scenario_id")),
            "UNCLASSIFIED_INTRINSIC_REPRICING_PATH",
        )
    return records


def _global_signal_summary(
    signals_by_time_ns: Mapping[int, tuple[Any, ...]],
) -> dict[str, Any]:
    execution.runner.FAMILY_MODE = "both"
    summary = _ORIGINAL_GLOBAL_SIGNAL_SUMMARY(signals_by_time_ns)
    signals = [signal for values in signals_by_time_ns.values() for signal in values]
    summary["implementation_revision"] = IMPLEMENTATION_REVISION
    summary["intrinsic_repricing_path_mode"] = _active_path_mode()
    summary["by_entry_path"] = dict(
        sorted(Counter(_signal_path(signal) for signal in signals).items())
    )
    summary["ten_second_cadence_contract"] = "EXACT_CONSECUTIVE_10_SECONDS"
    return summary


def _path_execution_summary(
    results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    signal_counts: Counter[str] = Counter()
    trade_counts: Counter[str] = Counter()
    wins: Counter[str] = Counter()
    pnl: defaultdict[str, float] = defaultdict(float)
    for result in results:
        detector = result.get("detector", {})
        if isinstance(detector, Mapping):
            raw = detector.get("by_entry_path", {})
            if isinstance(raw, Mapping):
                for path, count in raw.items():
                    signal_counts[str(path)] += int(count)
        for trade in result.get("closed_trade_records", []):
            path = str(trade.get("entry_path", "UNCLASSIFIED_INTRINSIC_REPRICING_PATH"))
            value = float(trade.get("realized_pnl", 0.0))
            trade_counts[path] += 1
            wins[path] += int(value > 0.0)
            pnl[path] += value

    paths = sorted(
        {
            DIRECT_PERSISTENCE_PATH,
            REPRICE_RESUMPTION_PATH,
            *signal_counts,
            *trade_counts,
        }
    )
    return {
        path: {
            "signals": int(signal_counts[path]),
            "closed_trades": int(trade_counts[path]),
            "wins": int(wins[path]),
            "losses": int(trade_counts[path] - wins[path]),
            "win_rate": (
                float(wins[path] / trade_counts[path]) if trade_counts[path] else 0.0
            ),
            "realized_pnl_usdt": float(pnl[path]),
        }
        for path in paths
    }


def _suite_summary(
    config: Mapping[str, Any],
    suite: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    execution.runner.FAMILY_MODE = "both"
    summary = _ORIGINAL_SUITE_SUMMARY(config, suite, results)
    mode = _active_path_mode()
    base_mode = mode == "both_paths"
    signals = [
        signal
        for result in results
        for values in result.get("_signals_for_attribution", {}).values()
        for signal in values
    ]
    # Signal objects are not persisted in result dictionaries.  The detector summaries and every
    # closed-trade record provide an independent count contract instead.
    detector_signal_count = sum(
        int(result.get("detector", {}).get("signals", 0)) for result in results
    )
    detector_intrinsic_count = sum(
        int(result.get("detector", {}).get("by_scenario_family", {}).get(
            INTRINSIC_REPRICING_FAMILY,
            0,
        ))
        for result in results
    )
    closed = [
        trade
        for result in results
        for trade in result.get("closed_trade_records", [])
    ]
    family_complete = (
        detector_signal_count == detector_intrinsic_count
        and all(
            str(trade.get("scenario_family")) == INTRINSIC_REPRICING_FAMILY
            for trade in closed
        )
    )
    path_results = _path_execution_summary(results)
    classified_signals = sum(item["signals"] for item in path_results.values())
    classified_trades = sum(item["closed_trades"] for item in path_results.values())
    path_complete = (
        classified_signals == detector_signal_count
        and classified_trades == int(summary.get("closed_trades", 0))
        and "UNCLASSIFIED_INTRINSIC_REPRICING_PATH" not in path_results
    )

    path_diagnostics = summarize_trade_path_diagnostics(closed)
    closed_count = int(summary.get("closed_trades", 0))
    revision_counts = Counter(
        str(trade.get("path_diagnostic", {}).get("diagnostic_revision"))
        for trade in closed
    )
    if closed_count == 0:
        revision_counts = Counter({DIAGNOSTIC_REVISION: 0})
    complete_paths = (
        int(path_diagnostics.get("records", -1)) == closed_count
        and int(path_diagnostics.get("complete_records", -1)) == closed_count
        and revision_counts == Counter({DIAGNOSTIC_REVISION: closed_count})
    )
    path_diagnostics["diagnostic_revision_counts"] = dict(sorted(revision_counts.items()))
    path_diagnostics["expected_diagnostic_revision"] = DIAGNOSTIC_REVISION

    summary["implementation_revision"] = IMPLEMENTATION_REVISION
    summary["intrinsic_repricing_path_mode"] = mode
    summary["diagnostic_path_ablation"] = not base_mode
    summary["promotable"] = bool(summary.get("promotable", True) and base_mode)
    summary["single_scenario_family"] = INTRINSIC_REPRICING_FAMILY
    summary["entry_path_results"] = path_results
    summary["single_family_attribution_passed"] = family_complete
    summary["entry_path_attribution_passed"] = path_complete
    summary["trade_path_diagnostic_revision"] = DIAGNOSTIC_REVISION
    summary["trade_path_diagnostic_summary"] = path_diagnostics
    summary["ten_second_cadence_contract"] = "EXACT_CONSECUTIVE_10_SECONDS"
    checks = summary.setdefault("suite_gate_checks", {})
    checks["single_intrinsic_repricing_family_attributed"] = family_complete
    checks["complete_intrinsic_entry_path_attribution"] = path_complete
    checks["complete_post_run_trade_path_diagnostics"] = complete_paths
    checks["base_contract_includes_both_entry_paths"] = base_mode
    summary["suite_gate_passed"] = bool(
        summary.get("suite_gate_passed", False)
        and family_complete
        and path_complete
        and complete_paths
        and base_mode
    )
    return summary


execution.runner.base_runner._closed_trade_records = _closed_trade_records
execution.runner.base_runner._global_signal_summary = _global_signal_summary
execution.runner.base_runner._suite_summary = _suite_summary


if __name__ == "__main__":
    raise SystemExit(execution.runner.base_runner.main())
