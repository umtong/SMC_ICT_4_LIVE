"""Causal first-retest ensemble across five- and fifteen-second structures."""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import json
from typing import Any, Mapping

import pandas as pd

from event_signal_data import CausalTradeSignal
from exact_timestamp_context import (
    completed_second_label,
    exact_local_bar_timestamps,
)
from nautilus_trader.model.identifiers import InstrumentId
import run_local_liquidity_sweep_mss_retest as local
from multiclock_sweep_mss_scenario import (
    discover_five_second,
)
from nested_liquidity_sweep_scenario import (
    discover as discover_fifteen_second,
)


_BASE_SIGNAL_BUILDER = local.build_causal_signals


def episode_key(item: Mapping[str, Any]) -> tuple[str, int, str]:
    """Identify one physical source-liquidity episode across clock encodings.

    The same completed 15-second source bar can appear as the final nanosecond of
    one wall-clock second on one path and as the exact next-second boundary after
    a pandas float coercion on another. Episode ownership therefore uses the
    causal completed-second label, while exact timestamps remain in the payload.
    """
    return (
        str(item["source_pool_id"]),
        completed_second_label(int(item["sweep"]["timestamp_ns"])),
        str(item["direction"]),
    )


def select_first_retests(
    five_report: Mapping[str, Any],
    fifteen_report: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Consume each sweep at its first completed valid retest.

    This offline selection is equivalent to a live state machine which emits the
    first completed retest and permanently consumes the source episode. A later
    confirmation from the other clock cannot resurrect the same sweep.
    """
    grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    source_labels: dict[tuple[str, str], set[int]] = defaultdict(set)
    candidate_counts: Counter[str] = Counter()
    for execution_timeframe, report in (
        ("5S", five_report),
        ("15S", fifteen_report),
    ):
        for raw in report.get("scenarios", ()):
            if raw.get("outcome") != "ENTRY_READY":
                continue
            item = deepcopy(dict(raw))
            item["execution_timeframe"] = execution_timeframe
            item["structural_family"] = "15S_sweep_multiclock_first_retest"
            key = episode_key(item)
            grouped[key].append(item)
            source_labels[(key[0], key[2])].add(key[1])
            candidate_counts[execution_timeframe] += 1

    inconsistent_sources = {
        f"{source_pool_id}:{direction}": sorted(labels)
        for (source_pool_id, direction), labels in source_labels.items()
        if len(labels) > 1
    }
    if inconsistent_sources:
        raise RuntimeError(
            "one consumed source pool produced multiple physical sweep seconds: "
            + json.dumps(inconsistent_sources, sort_keys=True)
        )

    selected: list[dict[str, Any]] = []
    chosen_counts: Counter[str] = Counter()
    discarded_counts: Counter[str] = Counter()
    duplicate_episodes = 0
    endpoint_precision_collisions = 0
    for key, candidates in grouped.items():
        if len(candidates) > 1:
            duplicate_episodes += 1
        raw_sweep_timestamps = sorted(
            {int(item["sweep"]["timestamp_ns"]) for item in candidates}
        )
        if len(raw_sweep_timestamps) > 1:
            endpoint_precision_collisions += 1
        chosen = min(
            candidates,
            key=lambda item: (
                int(item["observed_time_ns"]),
                0 if item["execution_timeframe"] == "15S" else 1,
                str(item["scenario_id"]),
            ),
        )
        chosen["episode_key"] = {
            "source_pool_id": key[0],
            "completed_sweep_second": key[1],
            "direction": key[2],
            "sweep_timestamp_ns": int(chosen["sweep"]["timestamp_ns"]),
            "candidate_sweep_timestamps_ns": raw_sweep_timestamps,
        }
        chosen["episode_selection"] = "FIRST_COMPLETED_VALID_RETEST"
        selected.append(chosen)
        chosen_counts[str(chosen["execution_timeframe"])] += 1
        for item in candidates:
            if item is not chosen:
                discarded_counts[str(item["execution_timeframe"])] += 1

    selected.sort(
        key=lambda item: (
            int(item["observed_time_ns"]),
            str(item["scenario_id"]),
        )
    )
    diagnostics = {
        "candidate_counts": dict(sorted(candidate_counts.items())),
        "source_episodes": len(grouped),
        "duplicate_clock_episodes": duplicate_episodes,
        "endpoint_precision_collisions": endpoint_precision_collisions,
        "selected_counts": dict(sorted(chosen_counts.items())),
        "discarded_later_confirmation_counts": dict(
            sorted(discarded_counts.items())
        ),
        "selection_rule": "first completed valid retest consumes the source episode",
        "episode_identity": (
            "source pool + completed wall-clock second + direction; exact "
            "nanoseconds retained as evidence"
        ),
        "future_information": False,
    }
    return selected, diagnostics


def discover_ensemble(
    *,
    config: Mapping[str, Any],
    bundle: Any,
    start: Any,
    end: Any,
    require_retest: bool,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not require_retest:
        raise ValueError("multiclock ensemble freezes the useful retest transition")
    _, upstream_5, selected_5, contract_5 = discover_five_second(
        config=config,
        bundle=bundle,
        start=start,
        end=end,
        require_retest=True,
    )
    with exact_local_bar_timestamps():
        _, upstream_15, selected_15, contract_15 = discover_fifteen_second(
            config=config,
            bundle=bundle,
            start=start,
            end=end,
            require_retest=True,
            include_higher_sources=False,
        )
    scenarios, selection = select_first_retests(selected_5, selected_15)
    active_days = sorted(
        {
            pd.to_datetime(int(item["observed_time_ns"]), unit="ns", utc=True)
            .date()
            .isoformat()
            for item in scenarios
        }
    )
    summary = {
        "require_retest": True,
        "family": "15S_sweep_multiclock_first_retest",
        "entry_ready": len(scenarios),
        "active_days": len(active_days),
        "active_day_labels": active_days,
        "five_second_summary": selected_5["summary"],
        "fifteen_second_summary": selected_15["summary"],
        "selection": selection,
        "orders_or_pnl": False,
        "future_information": False,
    }
    selected = {"summary": summary, "scenarios": scenarios}
    upstream = {
        "summary": summary,
        "scenarios": scenarios,
        "five_second_upstream": upstream_5,
        "fifteen_second_upstream": upstream_15,
    }
    contract = {
        "family": "15S_sweep_multiclock_first_retest",
        "source_timeframe": "15S",
        "execution_timeframes": ["5S", "15S"],
        "episode_arbitration": selection["selection_rule"],
        "episode_identity": selection["episode_identity"],
        "exact_fifteen_second_timestamps": True,
        "five_second_contract": contract_5,
        "fifteen_second_contract": contract_15,
        "selected_summary": summary,
        "single_pending_or_open_slot": True,
        "orders_or_pnl_created_by_preprocessor": False,
        "future_information": False,
        "implementation_clean": bool(contract_5.get("implementation_clean"))
        and bool(contract_15.get("implementation_clean")),
    }
    return bundle.seconds, upstream, selected, contract


def build_ensemble_signals(
    *,
    report: Mapping[str, Any],
    upstream_report: Mapping[str, Any],
    instrument_id: InstrumentId,
) -> list[CausalTradeSignal]:
    base_signals = _BASE_SIGNAL_BUILDER(
        report=report,
        upstream_report=upstream_report,
        instrument_id=instrument_id,
    )
    scenarios = {
        str(item["scenario_id"]): item
        for item in report.get("scenarios", ())
    }
    output: list[CausalTradeSignal] = []
    for signal in base_signals:
        scenario = scenarios[signal.scenario_id]
        details = json.loads(signal.details_json)
        details.update(
            {
                "structural_family": "15S_sweep_multiclock_first_retest",
                "source_timeframe": "15S",
                "execution_timeframe": scenario["execution_timeframe"],
                "episode_key": scenario["episode_key"],
                "episode_selection": scenario["episode_selection"],
            }
        )
        serialized = json.dumps(details, sort_keys=True)
        for forbidden in ("path", "mfe", "mae", "terminal", "realized_r"):
            if forbidden in serialized.lower():
                raise RuntimeError(f"future-path field leaked into signal: {forbidden}")
        output.append(
            CausalTradeSignal(
                instrument_id=signal.instrument_id,
                scenario_id=signal.scenario_id,
                direction=signal.direction,
                entry_reference=signal.entry_reference,
                stop_price=signal.stop_price,
                target_price=signal.target_price,
                expected_rr=signal.expected_rr,
                source_pool_id=signal.source_pool_id,
                signal_kind="MULTICLOCK_SWEEP_FIRST_RETEST",
                details_json=serialized,
                observed_time_ns=signal.observed_time_ns,
                ts_event=signal.ts_event,
                ts_init=signal.ts_init,
            )
        )
    output.sort(key=lambda item: (item.ts_event, item.scenario_id))
    return output


__all__ = [
    "build_ensemble_signals",
    "discover_ensemble",
    "episode_key",
    "select_first_retests",
]
