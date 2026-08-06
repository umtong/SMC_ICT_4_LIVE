"""Run candidate-08 auction-router signals through the verified shared-account runner.

The shared execution adapter intentionally remains unchanged.  Auction-specific scenario metadata is
carried by each immutable signal in ``logic_details`` and normalized here immediately before the
existing reporting helpers consume the completed NautilusTrader run.  This keeps execution, risk,
funding, and liquidation behavior identical to the already verified production adapter while still
producing truthful per-scenario diagnostics.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

import run_aggtrade_acceptance_nautilus as base_runner

from aggtrade_auction_router_signals import build_auction_router_signals


_original_position_metrics = base_runner._position_metrics
_original_closed_trade_records = base_runner._closed_trade_records
_original_global_signal_summary = base_runner._global_signal_summary


def _scenario_family_from_intent(intent: Mapping[str, Any]) -> str:
    details = intent.get("logic_details", {})
    if isinstance(details, Mapping):
        value = details.get("scenario_family")
        if value:
            return str(value)
    return str(intent.get("scenario_family", "UNCLASSIFIED_AUCTION_SCENARIO"))


def _normalize_intent_scenario_families(intents: list[dict[str, Any]]) -> None:
    """Mutate only reporting metadata after execution has completed."""

    for intent in intents:
        intent["scenario_family"] = _scenario_family_from_intent(intent)


def _auction_position_metrics(
    positions: Any,
    intents: list[dict[str, Any]],
    position_outcomes: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _normalize_intent_scenario_families(intents)
    return _original_position_metrics(positions, intents, position_outcomes)


def _auction_closed_trade_records(
    enriched_positions: list[dict[str, Any]],
    intents: list[dict[str, Any]],
    position_outcomes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    _normalize_intent_scenario_families(intents)
    records = _original_closed_trade_records(
        enriched_positions,
        intents,
        position_outcomes,
    )
    family_by_scenario = {
        str(intent.get("scenario_id")): _scenario_family_from_intent(intent)
        for intent in intents
    }
    for record in records:
        record["scenario_family"] = family_by_scenario.get(
            str(record.get("scenario_id")),
            "UNCLASSIFIED_AUCTION_SCENARIO",
        )
    return records


def _auction_global_signal_summary(
    signals_by_time_ns: Mapping[int, tuple[Any, ...]],
) -> dict[str, Any]:
    summary = _original_global_signal_summary(signals_by_time_ns)
    signals = [signal for items in signals_by_time_ns.values() for signal in items]
    summary["by_scenario_family"] = dict(
        sorted(
            Counter(
                str(signal.details.get("scenario_family", "UNCLASSIFIED_AUCTION_SCENARIO"))
                for signal in signals
            ).items()
        )
    )
    return summary


base_runner.build_acceptance_signals = build_auction_router_signals
base_runner._position_metrics = _auction_position_metrics
base_runner._closed_trade_records = _auction_closed_trade_records
base_runner._global_signal_summary = _auction_global_signal_summary


if __name__ == "__main__":
    raise SystemExit(base_runner.main())
