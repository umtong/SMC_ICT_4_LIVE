"""Combine parent-accepted five-second retests with full fifteen-second retests."""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict
import json
from typing import Any, Mapping

import pandas as pd

import backtest as base
import diagnose_impact_resilience_1s as impact
from diagnose_aggtrade_resilience_v2 import preconsume_before_event_window
from diagnose_failed_flow import aggregate_flow
from event_signal_data import CausalTradeSignal
from five_second_parent_acceptance import diagnose_parent_acceptance
from nautilus_trader.model.identifiers import InstrumentId
from nested_liquidity_sweep_scenario import (
    discover as discover_fifteen_second,
)
import run_local_liquidity_sweep_mss_retest as local


_BASE_SIGNAL_BUILDER = local.build_causal_signals


def _episode_key(item: Mapping[str, Any]) -> tuple[str, int, str]:
    return (
        str(item["source_pool_id"]),
        int(item["sweep"]["timestamp_ns"]),
        str(item["direction"]),
    )


def select_parent_accepted_or_full_retest(
    parent_report: Mapping[str, Any],
    fifteen_report: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Consume a sweep at the first valid parent-accepted or full 15S retest."""
    grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    candidate_counts: Counter[str] = Counter()
    for label, report in (
        ("5S_RETEST_15S_ACCEPTANCE", parent_report),
        ("15S_RETEST", fifteen_report),
    ):
        for raw in report.get("scenarios", ()):
            if raw.get("outcome") != "ENTRY_READY":
                continue
            item = deepcopy(dict(raw))
            item["execution_timeframe"] = label
            item["structural_family"] = "parent_accepted_multiclock_retest"
            grouped[_episode_key(item)].append(item)
            candidate_counts[label] += 1

    selected: list[dict[str, Any]] = []
    selected_counts: Counter[str] = Counter()
    discarded_counts: Counter[str] = Counter()
    duplicate_episodes = 0
    for key, candidates in grouped.items():
        if len(candidates) > 1:
            duplicate_episodes += 1
        chosen = min(
            candidates,
            key=lambda item: (
                int(item["observed_time_ns"]),
                0 if item["execution_timeframe"] == "15S_RETEST" else 1,
                str(item["scenario_id"]),
            ),
        )
        chosen["episode_key"] = {
            "source_pool_id": key[0],
            "sweep_timestamp_ns": key[1],
            "direction": key[2],
        }
        chosen["episode_selection"] = (
            "FIRST_PARENT_ACCEPTED_5S_OR_FULL_15S_RETEST"
        )
        selected.append(chosen)
        selected_counts[str(chosen["execution_timeframe"])] += 1
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
        "selected_counts": dict(sorted(selected_counts.items())),
        "discarded_later_confirmation_counts": dict(
            sorted(discarded_counts.items())
        ),
        "selection_rule": (
            "first completed parent-accepted 5S retest or full 15S retest "
            "permanently consumes the source sweep"
        ),
        "future_information": False,
    }
    return selected, diagnostics


def discover_parent_accepted(
    *,
    config: Mapping[str, Any],
    bundle: Any,
    start: Any,
    end: Any,
    require_retest: bool,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any], dict[str, Any]]:
    del config
    if not require_retest:
        raise ValueError("parent-acceptance successor freezes the retest transition")
    logic = local.LocalSweepMSSLogic()
    logic.validate()
    minute = impact._minute_features(
        bundle.minute_positioning.frame,
        atr_period=impact.ImpactLogic().minute_atr_period,
    )
    five = aggregate_flow(bundle.minute_positioning.frame, 5, 36)
    five["timestamp_ns"] = five["timestamp_ns"].astype("int64")
    event_start_ns = int(bundle.seconds.iloc[0]["timestamp_ns"])
    one_all = impact._pool_confirmations(
        minute,
        timeframe="1M",
        radius=impact.ImpactLogic().one_minute_pivot_radius,
    )
    five_all = impact._pool_confirmations(
        five,
        timeframe="5M",
        radius=impact.ImpactLogic().five_minute_pivot_radius,
    )
    one_pools, one_pre = preconsume_before_event_window(
        one_all,
        minute,
        event_start_ns=event_start_ns,
    )
    five_pools, five_pre = preconsume_before_event_window(
        five_all,
        minute,
        event_start_ns=event_start_ns,
    )
    selected_parent = diagnose_parent_acceptance(
        bundle.seconds,
        one_pools=one_pools,
        five_pools=five_pools,
        trade_start_ns=base._utc_ns(start),
        trade_end_ns=base._utc_ns(end),
        logic=logic,
    )
    upstream = {
        "summary": selected_parent["summary"],
        "scenarios": selected_parent["scenarios"],
    }
    contract = {
        "family": "15S_sweep_5S_retest_parent_15S_acceptance",
        "logic": asdict(logic),
        "one_minute_preconsumption": one_pre,
        "five_minute_preconsumption": five_pre,
        "selected_summary": selected_parent["summary"],
        "loader_diagnostics": dict(bundle.diagnostics),
        "implementation_clean": (
            int(bundle.diagnostics.get("out_of_order_rows", -1)) == 0
            and int(bundle.diagnostics.get("duplicate_agg_trade_ids", -1)) == 0
            and int(bundle.diagnostics.get("noncontiguous_second_transitions", -1)) == 0
            and int(bundle.diagnostics.get("missing_seconds_from_span", -1)) == 0
        ),
        "orders_or_pnl_created_by_preprocessor": False,
        "future_information": False,
    }
    return bundle.seconds, upstream, selected_parent, contract


def discover_parent_acceptance_ensemble(
    *,
    config: Mapping[str, Any],
    bundle: Any,
    start: Any,
    end: Any,
    require_retest: bool,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any], dict[str, Any]]:
    _, parent_upstream, parent_selected, parent_contract = discover_parent_accepted(
        config=config,
        bundle=bundle,
        start=start,
        end=end,
        require_retest=require_retest,
    )
    _, fifteen_upstream, fifteen_selected, fifteen_contract = discover_fifteen_second(
        config=config,
        bundle=bundle,
        start=start,
        end=end,
        require_retest=True,
        include_higher_sources=False,
    )
    scenarios, selection = select_parent_accepted_or_full_retest(
        parent_selected,
        fifteen_selected,
    )
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
        "family": "parent_accepted_multiclock_retest",
        "entry_ready": len(scenarios),
        "active_days": len(active_days),
        "active_day_labels": active_days,
        "parent_acceptance_summary": parent_selected["summary"],
        "fifteen_second_summary": fifteen_selected["summary"],
        "selection": selection,
        "orders_or_pnl": False,
        "future_information": False,
    }
    selected = {"summary": summary, "scenarios": scenarios}
    upstream = {
        "summary": summary,
        "scenarios": scenarios,
        "parent_acceptance_upstream": parent_upstream,
        "fifteen_second_upstream": fifteen_upstream,
    }
    contract = {
        "family": "parent_accepted_multiclock_retest",
        "source_timeframe": "15S",
        "execution_paths": ["5S_RETEST_15S_ACCEPTANCE", "15S_RETEST"],
        "episode_arbitration": selection["selection_rule"],
        "parent_acceptance_contract": parent_contract,
        "fifteen_second_contract": fifteen_contract,
        "selected_summary": summary,
        "single_pending_or_open_slot": True,
        "orders_or_pnl_created_by_preprocessor": False,
        "future_information": False,
        "implementation_clean": bool(parent_contract["implementation_clean"])
        and bool(fifteen_contract["implementation_clean"]),
    }
    return bundle.seconds, upstream, selected, contract


def build_parent_acceptance_ensemble_signals(
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
        if scenario.get("parent_acceptance") is not None:
            details["parent_acceptance"] = scenario["parent_acceptance"]
        details.update(
            {
                "structural_family": "parent_accepted_multiclock_retest",
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
                signal_kind="PARENT_ACCEPTED_MULTICLOCK_RETEST",
                details_json=serialized,
                observed_time_ns=signal.observed_time_ns,
                ts_event=signal.ts_event,
                ts_init=signal.ts_init,
            )
        )
    output.sort(key=lambda item: (item.ts_event, item.scenario_id))
    return output


__all__ = [
    "build_parent_acceptance_ensemble_signals",
    "discover_parent_acceptance_ensemble",
    "discover_parent_accepted",
    "select_parent_accepted_or_full_retest",
]
