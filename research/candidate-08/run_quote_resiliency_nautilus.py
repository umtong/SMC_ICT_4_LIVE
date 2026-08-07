"""Run external-liquidity quote-resiliency through the verified native Nautilus stack.

Only data enrichment, detector selection, scenario labels, variable-length event serialization and
post-run reporting are adapted.  The incumbent runner continues to own the shared Binance margin
account, one global entry/position constraint, current-NAV three-percent sizing, market OUO bracket,
fees, causal entry/stop reserves, official funding and mark prices, liquidation, fills and account
reports.  No separate backtest engine is introduced.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

import run_aggtrade_flow_response_auction_nautilus as execution
from aggtrade_acceptance_risk_v2 import (
    RISK_ACCOUNTING_REVISION,
    run_window_classifying_execution_risk,
)
from flow_response_trade_path_diagnostics_v2 import (
    DIAGNOSTIC_REVISION,
    summarize_trade_path_diagnostics,
)
from quote_resiliency_data_v2 import DATA_REVISION, load_completed_quote_buckets
from quote_resiliency_features_v3 import (
    IMPLEMENTATION_REVISION as FEATURE_REVISION,
    QuoteResiliencyConfig,
    build_quote_resiliency_features,
)
from quote_resiliency_signals import (
    CONTINUATION_FAMILY,
    REVERSAL_FAMILY,
    SIGNAL_REVISION,
    QuoteResiliencySignalBundle,
    build_quote_resiliency_signals,
)
from quote_resiliency_strategy import (
    EXECUTION_ADAPTER_REVISION,
    QuoteResiliencyExecutionStrategy,
)


CONFIG_IMPLEMENTATION_REVISION = "CAUSAL_EXTERNAL_LIQUIDITY_QUOTE_RESILIENCY_V1"
RUNNER_REVISION = "QUOTE_RESILIENCY_NATIVE_NAUTILUS_ADAPTER_V1"
UNCLASSIFIED_FAMILY = "UNCLASSIFIED_QUOTE_RESILIENCY_SCENARIO"
BASE_ABLATION = "none"
OFI_ABLATION = "remove_confirmation_quote_ofi_direction_gate"
ABLATIONS = frozenset((BASE_ABLATION, OFI_ABLATION))
DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent / "config_quote_resiliency_btc_v1.json"
)
DEFAULT_PATTERN_CONFIG = Path(__file__).resolve().parent / "config_range_fvg.json"

_ACTIVE_ABLATION = BASE_ABLATION


def _config_path() -> Path:
    return DEFAULT_CONFIG_PATH.resolve()


def _load_payload() -> dict[str, Any]:
    payload = json.loads(_config_path().read_text(encoding="utf-8"))
    revision = str(payload.get("implementation_revision", ""))
    if revision != CONFIG_IMPLEMENTATION_REVISION:
        raise RuntimeError(
            "quote-resiliency implementation/config mismatch: "
            f"{revision!r} != {CONFIG_IMPLEMENTATION_REVISION!r}"
        )
    risk_fraction = float(payload["risk_fraction"])
    if risk_fraction <= 0.0 or risk_fraction > 0.03:
        raise RuntimeError("quote-resiliency risk_fraction must be in (0, 0.03]")
    return payload


def _feature_config() -> QuoteResiliencyConfig:
    config = QuoteResiliencyConfig(**dict(_load_payload()["quote_resiliency_config"]))
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


def _build_signals(**kwargs: Any) -> QuoteResiliencySignalBundle:
    # The verified base runner supplies its incumbent ablation keyword.  It has no meaning for this
    # detector and is removed rather than silently mapped to a quote condition.
    kwargs.pop("require_retest_contraction", None)
    config = _feature_config()
    quote_gate = _ACTIVE_ABLATION != OFI_ABLATION
    return build_quote_resiliency_signals(
        **kwargs,
        config=config,
        quote_ofi_confirmation_required=quote_gate,
    )


_ORIGINAL_CAPTURED_AGGTRADE_LOADER = execution._capturing_load_ten_second_aggtrades


def _load_trade_and_quote_features(*args: Any, **kwargs: Any):
    """Join two checksum-verified completed streams without altering replay OHLC paths."""

    trade_frame, trade_sources, trade_quality = _ORIGINAL_CAPTURED_AGGTRADE_LOADER(
        *args,
        **kwargs,
    )
    symbol = kwargs.get("symbol")
    start = kwargs.get("start")
    end = kwargs.get("end")
    cache_dir = kwargs.get("cache_dir")
    if symbol is None and args:
        symbol = args[0]
    if symbol is None or start is None or end is None or cache_dir is None:
        raise RuntimeError("quote-resiliency loader requires symbol/start/end/cache_dir keywords")
    quote_frame, quote_sources, quote_quality = load_completed_quote_buckets(
        symbol=str(symbol),
        start=start,
        end=end,
        cache_dir=Path(cache_dir).resolve().parent / "bookTicker",
        cadence_seconds=10,
    )
    tick = float(_load_payload()["assets"][str(symbol)]["tick_size"])
    features = build_quote_resiliency_features(
        trade_bars=trade_frame,
        quote_buckets=quote_frame,
        tick=tick,
        config=_feature_config(),
    )
    if not features.index.equals(trade_frame.index):
        raise RuntimeError("quote feature join changed the authoritative trade replay index")
    if len(features.index) != int(trade_quality.get("rows", len(trade_frame.index))):
        raise RuntimeError("quote feature join changed the authoritative trade replay row count")

    quality = dict(trade_quality)
    quality["quote_resiliency"] = {
        "data_revision": DATA_REVISION,
        "feature_revision": FEATURE_REVISION,
        "rows": len(features.index),
        "observable_rows": int(features["quote_resiliency_observable"].sum()),
        "unobservable_rows": int((~features["quote_resiliency_observable"]).sum()),
        "quote_quality": quote_quality,
        "quote_files": [
            {
                "symbol": source.symbol,
                "day": source.day,
                "url": source.url,
                "checksum_url": source.checksum_url,
                "sha256": source.sha256,
                "size_bytes": source.size_bytes,
                "archive_member": source.archive_member,
                "valid_rows": source.valid_rows,
            }
            for source in quote_sources
        ],
        "join_contract": "TRADE_BUCKET_INDEX_AUTHORITATIVE_QUOTE_STATE_CAUSALLY_FORWARD_FILLED",
        "raw_equal_timestamp_contract": (
            "TRANSACTION_TIME_THEN_UPDATE_ID_STABLE_ORDER_DUPLICATES_PRESERVED"
        ),
    }
    return features, trade_sources, quality


def _write_merged_events(
    path: Path,
    *,
    signals_by_time_ns: Mapping[int, tuple[Any, ...]],
    execution_events: list[dict[str, Any]],
) -> int:
    """Serialize every causal state transition; reversal and continuation lengths may differ."""

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
                        None if reference is None else format(float(reference), ".12g")
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


_ORIGINAL_GLOBAL_SIGNAL_SUMMARY = execution.runner._auction_global_signal_summary
_ORIGINAL_SUITE_SUMMARY = execution.runner._auction_suite_summary
_NATIVE_ORIGINAL_RUN_WINDOW = execution._original_run_window


def _global_signal_summary(
    signals_by_time_ns: Mapping[int, tuple[Any, ...]],
) -> dict[str, Any]:
    execution.runner.FAMILY_MODE = "both"
    summary = _ORIGINAL_GLOBAL_SIGNAL_SUMMARY(signals_by_time_ns)
    summary["implementation_revision"] = CONFIG_IMPLEMENTATION_REVISION
    summary["runner_revision"] = RUNNER_REVISION
    summary["signal_revision"] = SIGNAL_REVISION
    summary["feature_revision"] = FEATURE_REVISION
    summary["data_revision"] = DATA_REVISION
    summary["execution_adapter_revision"] = EXECUTION_ADAPTER_REVISION
    summary["risk_accounting_revision"] = RISK_ACCOUNTING_REVISION
    summary["quote_ofi_confirmation_required"] = _ACTIVE_ABLATION == BASE_ABLATION
    summary["ablation"] = _ACTIVE_ABLATION
    summary["ten_second_cadence_contract"] = "EXACT_CONSECUTIVE_COMPLETED_10_SECONDS"
    return summary


def _suite_summary(
    config: Mapping[str, Any],
    suite: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    execution.runner.FAMILY_MODE = "both"
    summary = _ORIGINAL_SUITE_SUMMARY(config, suite, results)
    base_contract = _ACTIVE_ABLATION == BASE_ABLATION
    summary.update(
        {
            "implementation_revision": CONFIG_IMPLEMENTATION_REVISION,
            "runner_revision": RUNNER_REVISION,
            "signal_revision": SIGNAL_REVISION,
            "feature_revision": FEATURE_REVISION,
            "data_revision": DATA_REVISION,
            "execution_adapter_revision": EXECUTION_ADAPTER_REVISION,
            "risk_accounting_revision": RISK_ACCOUNTING_REVISION,
            "trade_path_diagnostic_revision": DIAGNOSTIC_REVISION,
            "ablation": _ACTIVE_ABLATION,
            "diagnostic_only_ablation": not base_contract,
            "quote_ofi_confirmation_required": base_contract,
            "scenario_contract": (
                "COMPLETED_EXTERNAL_LIQUIDITY_INTERACTION_THEN_DISPLAYED_QUOTE_RESPONSE_"
                "THEN_SEPARATE_PRICE_AND_FLOW_CONFIRMATION"
            ),
            "ten_second_cadence_contract": "EXACT_CONSECUTIVE_COMPLETED_10_SECONDS",
        }
    )
    closed_trades = [
        trade
        for result in results
        for trade in result.get("closed_trade_records", [])
    ]
    path_summary = summarize_trade_path_diagnostics(closed_trades)
    closed_count = int(summary.get("closed_trades", 0))
    revision_counts = Counter(
        str(trade.get("path_diagnostic", {}).get("diagnostic_revision"))
        for trade in closed_trades
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
    summary["trade_path_diagnostic_summary"] = path_summary
    summary["promotable"] = bool(summary.get("promotable", True) and base_contract)
    checks = summary.setdefault("suite_gate_checks", {})
    checks["base_quote_ofi_confirmation_contract"] = base_contract
    checks["complete_post_run_trade_path_diagnostics"] = path_complete
    checks["both_quote_resiliency_families_enabled"] = True
    summary["suite_gate_passed"] = bool(
        summary.get("suite_gate_passed", False)
        and base_contract
        and path_complete
    )
    return summary


def _run_window_with_risk_classification(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return run_window_classifying_execution_risk(
        _NATIVE_ORIGINAL_RUN_WINDOW,
        *args,
        **kwargs,
    )


# Rebind every adapter boundary before any suite runs.
execution.runner.INITIATIVE_FAMILY = CONTINUATION_FAMILY
execution.runner.FAILED_AUCTION_FAMILY = REVERSAL_FAMILY
execution.runner.UNCLASSIFIED_FAMILY = UNCLASSIFIED_FAMILY
execution.runner.FAMILY_MODE = "both"
execution.runner.FAMILY_MODES = {
    "both": frozenset((CONTINUATION_FAMILY, REVERSAL_FAMILY)),
}
execution.runner.build_auction_router_signals = build_quote_resiliency_signals
execution.runner._build_router_signals = _build_signals
execution.runner._signal_family = _signal_family
execution.runner.base_runner.build_acceptance_signals = _build_signals
execution.runner.base_runner.load_ten_second_aggtrades = _load_trade_and_quote_features
execution.runner.base_runner.AggTradeAcceptanceStrategy = QuoteResiliencyExecutionStrategy
execution.runner.base_runner._write_merged_events = _write_merged_events
execution.runner.base_runner._global_signal_summary = _global_signal_summary
execution.runner.base_runner._suite_summary = _suite_summary
# The flow-response run-window wrapper resolves this global at call time and continues to scope raw
# replay frames for complete post-run path diagnostics.
execution._original_run_window = _run_window_with_risk_classification


def run_suite(
    *,
    config_path: Path,
    pattern_config_path: Path,
    suite: str,
    output: Path,
    data_cache: Path,
    reuse_first_dir: Path | None = None,
    ablation: str = BASE_ABLATION,
) -> dict[str, Any]:
    global _ACTIVE_ABLATION
    if ablation not in ABLATIONS:
        raise ValueError(f"unsupported quote-resiliency ablation: {ablation!r}")
    previous = _ACTIVE_ABLATION
    _ACTIVE_ABLATION = ablation
    try:
        return execution.runner.base_runner.run_suite(
            config_path=config_path,
            pattern_config_path=pattern_config_path,
            suite=suite,
            output=output,
            data_cache=data_cache,
            reuse_first_dir=reuse_first_dir,
            ablation=ablation,
        )
    finally:
        _ACTIVE_ABLATION = previous


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("first", "screen"), default="first")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--pattern-config", type=Path, default=DEFAULT_PATTERN_CONFIG)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--ablation",
        choices=tuple(sorted(ABLATIONS)),
        default=BASE_ABLATION,
        help="The single predeclared diagnostic-only quote-OFI confirmation ablation.",
    )
    parser.add_argument("--reuse-first-dir", type=Path, default=None)
    parser.add_argument(
        "--data-cache",
        type=Path,
        default=Path.home() / ".cache" / "smc4" / "candidate-08-quote-resiliency",
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
        ablation=args.ablation,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
