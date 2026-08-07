"""Replay adapter for 15S source sweeps with five-second execution structure."""
from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any, Mapping

import pandas as pd

import backtest as base
import diagnose_impact_resilience_1s as impact
from diagnose_aggtrade_resilience_v2 import preconsume_before_event_window
from diagnose_failed_flow import aggregate_flow
from event_signal_data import CausalTradeSignal
from nautilus_trader.model.identifiers import InstrumentId
import run_local_liquidity_sweep_mss_retest as local
from five_second_flow_bars import scaled_execution_logic
from five_second_sweep_execution import diagnose_five_second_execution


_BASE_SIGNAL_BUILDER = local.build_causal_signals


def discover_five_second(
    *,
    config: Mapping[str, Any],
    bundle: Any,
    start: Any,
    end: Any,
    require_retest: bool,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any], dict[str, Any]]:
    del config
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
    selected = diagnose_five_second_execution(
        bundle.seconds,
        one_pools=one_pools,
        five_pools=five_pools,
        trade_start_ns=base._utc_ns(start),
        trade_end_ns=base._utc_ns(end),
        logic=logic,
        require_retest=require_retest,
    )
    upstream = {
        "summary": selected["summary"],
        "scenarios": selected["scenarios"],
    }
    contract = {
        "family": "15S_sweep_5S_MSS_retest",
        "variant": "five_second_execution",
        "source_logic": asdict(logic),
        "execution_logic": asdict(scaled_execution_logic(logic)),
        "source_timeframe": "15S",
        "execution_timeframe": "5S",
        "one_minute_preconsumption": one_pre,
        "five_minute_preconsumption": five_pre,
        "detector_population": "literal first touch of causal 15-second swing liquidity",
        "selected_summary": selected["summary"],
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
    return bundle.seconds, upstream, selected, contract


def build_five_second_signals(
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
    output: list[CausalTradeSignal] = []
    for signal in base_signals:
        details = json.loads(signal.details_json)
        details["structural_family"] = "15S_sweep_5S_MSS_retest"
        details["source_timeframe"] = "15S"
        details["execution_timeframe"] = "5S"
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
                signal_kind="15S_SWEEP_5S_MSS_BREAK_RETEST",
                details_json=serialized,
                observed_time_ns=signal.observed_time_ns,
                ts_event=signal.ts_event,
                ts_init=signal.ts_init,
            )
        )
    return output
