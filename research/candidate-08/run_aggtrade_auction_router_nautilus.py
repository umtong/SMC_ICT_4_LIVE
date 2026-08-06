"""Run candidate-08 auction-router signals through the verified shared-account runner.

The shared execution adapter intentionally remains unchanged. Auction-specific scenario metadata is
carried by each immutable signal in ``logic_details`` and normalized here immediately before the
existing reporting helpers consume the completed NautilusTrader run. This keeps execution, risk,
funding, liquidation, and orders identical to the already verified production adapter while still
producing truthful per-scenario diagnostics.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

import run_aggtrade_acceptance_nautilus as base_runner

from aggtrade_auction_router_signals import (
    FAILED_AUCTION_FAMILY,
    INITIATIVE_FAMILY,
    build_auction_router_signals,
)


UNCLASSIFIED_FAMILY = "UNCLASSIFIED_AUCTION_SCENARIO"
_original_position_metrics = base_runner._position_metrics
_original_closed_trade_records = base_runner._closed_trade_records
_original_global_signal_summary = base_runner._global_signal_summary
_original_suite_summary = base_runner._suite_summary


def _scenario_family_from_intent(intent: Mapping[str, Any]) -> str:
    details = intent.get("logic_details", {})
    if isinstance(details, Mapping):
        value = details.get("scenario_family")
        if value:
            return str(value)
    return str(intent.get("scenario_family", UNCLASSIFIED_FAMILY))


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
            UNCLASSIFIED_FAMILY,
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
                str(signal.details.get("scenario_family", UNCLASSIFIED_FAMILY))
                for signal in signals
            ).items()
        )
    )
    return summary


def _family_execution_summary(
    results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    signal_counts: Counter[str] = Counter()
    trade_counts: Counter[str] = Counter()
    wins: Counter[str] = Counter()
    realized_pnl: defaultdict[str, float] = defaultdict(float)
    close_reasons: defaultdict[str, Counter[str]] = defaultdict(Counter)
    symbols: defaultdict[str, Counter[str]] = defaultdict(Counter)

    for result in results:
        detector = result.get("detector", {})
        if isinstance(detector, Mapping):
            by_family = detector.get("by_scenario_family", {})
            if isinstance(by_family, Mapping):
                for family, count in by_family.items():
                    signal_counts[str(family)] += int(count)
        for trade in result.get("closed_trade_records", []):
            family = str(trade.get("scenario_family", UNCLASSIFIED_FAMILY))
            pnl = float(trade.get("realized_pnl", 0.0))
            trade_counts[family] += 1
            wins[family] += int(pnl > 0.0)
            realized_pnl[family] += pnl
            close_reasons[family][str(trade.get("close_reason"))] += 1
            symbols[family][str(trade.get("symbol"))] += 1

    families = sorted(
        {
            INITIATIVE_FAMILY,
            FAILED_AUCTION_FAMILY,
            *signal_counts,
            *trade_counts,
        }
    )
    by_family: dict[str, Any] = {}
    for family in families:
        trades = int(trade_counts[family])
        family_wins = int(wins[family])
        by_family[family] = {
            "signals": int(signal_counts[family]),
            "closed_trades": trades,
            "wins": family_wins,
            "losses": trades - family_wins,
            "win_rate": family_wins / trades if trades else 0.0,
            "realized_pnl_usdt": float(realized_pnl[family]),
            "close_reasons": dict(sorted(close_reasons[family].items())),
            "closed_trades_by_symbol": dict(sorted(symbols[family].items())),
        }

    attributed = sum(trade_counts.values())
    unclassified_trades = int(trade_counts[UNCLASSIFIED_FAMILY])
    unclassified_signals = int(signal_counts[UNCLASSIFIED_FAMILY])
    return {
        "by_family": by_family,
        "signals_attributed": int(sum(signal_counts.values())),
        "closed_trades_attributed": int(attributed),
        "unclassified_signals": unclassified_signals,
        "unclassified_closed_trades": unclassified_trades,
        "attribution_complete": (
            unclassified_signals == 0 and unclassified_trades == 0
        ),
    }


def _auction_suite_summary(
    config: Mapping[str, Any],
    suite: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = _original_suite_summary(config, suite, results)
    family_summary = _family_execution_summary(results)
    reported_signals = sum(
        int(result.get("detector", {}).get("signals", 0))
        for result in results
    )
    reported_closed_trades = int(summary.get("closed_trades", 0))
    checks = {
        "signals_attributed": family_summary["signals_attributed"],
        "reported_signals": reported_signals,
        "all_signals_attributed": (
            family_summary["signals_attributed"] == reported_signals
        ),
        "closed_trades_attributed": family_summary["closed_trades_attributed"],
        "reported_closed_trades": reported_closed_trades,
        "all_closed_trades_attributed": (
            family_summary["closed_trades_attributed"] == reported_closed_trades
        ),
        "no_unclassified_signals": family_summary["unclassified_signals"] == 0,
        "no_unclassified_closed_trades": (
            family_summary["unclassified_closed_trades"] == 0
        ),
    }
    attribution_passed = all(
        checks[name]
        for name in (
            "all_signals_attributed",
            "all_closed_trades_attributed",
            "no_unclassified_signals",
            "no_unclassified_closed_trades",
        )
    )

    summary["scenario_family_results"] = family_summary["by_family"]
    summary["scenario_attribution_checks"] = checks
    summary["scenario_attribution_passed"] = attribution_passed
    suite_checks = summary.setdefault("suite_gate_checks", {})
    suite_checks["complete_auction_scenario_attribution"] = attribution_passed
    summary["suite_gate_passed"] = bool(
        summary.get("suite_gate_passed", False) and attribution_passed
    )
    return summary


base_runner.build_acceptance_signals = build_auction_router_signals
base_runner._position_metrics = _auction_position_metrics
base_runner._closed_trade_records = _auction_closed_trade_records
base_runner._global_signal_summary = _auction_global_signal_summary
base_runner._suite_summary = _auction_suite_summary


if __name__ == "__main__":
    raise SystemExit(base_runner.main())
