#!/usr/bin/env python3
"""Controlled Nautilus ablation: prior 15-second close instead of VWAP.

The upstream volume-time impact event, entry time, stop, sizing, fees, slippage,
funding and Week-1 are unchanged.  This removes exactly one core variable from
the discarded baseline: volume weighting inside the already-completed
pre-contact fifteen-second bucket.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

import backtest_pre_attack_value as candidate
from diagnose_pre_attack_value import PreAttackValueLogic as OriginalValueLogic
from strategy_event_signal_safe import Candidate07SerializedEventStrategy


class _CloseLogicFactory:
    def __new__(
        cls,
        bucket_seconds: int = 15,
        target_statistic: str = "vwap",
    ) -> OriginalValueLogic:
        del target_statistic
        return OriginalValueLogic(
            bucket_seconds=bucket_seconds,
            target_statistic="close",
        )


_original_build = candidate.build_causal_signals
_original_run_week = candidate.run_week


def _build_close_signals(
    *,
    report: Mapping[str, Any],
    upstream_report: Mapping[str, Any],
    instrument_id: Any,
) -> list[Any]:
    signals = _original_build(
        report=report,
        upstream_report=upstream_report,
        instrument_id=instrument_id,
    )
    output: list[Any] = []
    for signal in signals:
        details = json.loads(signal.details_json)
        details["structural_family"] = "pre_attack_auction_close_ablation"
        details["target_statistic"] = "prior_complete_15s_close"
        output.append(
            candidate.CausalTradeSignal(
                instrument_id=signal.instrument_id,
                scenario_id=signal.scenario_id,
                direction=signal.direction,
                entry_reference=signal.entry_reference,
                stop_price=signal.stop_price,
                target_price=signal.target_price,
                expected_rr=signal.expected_rr,
                source_pool_id=signal.source_pool_id,
                signal_kind="PRE_ATTACK_VALUE_CLOSE_ABLATION",
                details_json=json.dumps(details, sort_keys=True),
                observed_time_ns=signal.observed_time_ns,
                ts_event=signal.ts_event,
                ts_init=signal.ts_init,
            )
        )
    return output


def _run_close_week(*args: Any, **kwargs: Any) -> dict[str, Any]:
    metrics = _original_run_week(*args, **kwargs)
    metrics["execution_contract"]["selected_route"] = (
        "volume-time failed auction to prior complete 15s close"
    )
    metrics["execution_contract"]["controlled_ablation"] = (
        "remove volume weighting only"
    )
    metrics["structural_contract"]["target"] = (
        "last price of the complete 15-second bucket before contact"
    )
    output = kwargs.get("output")
    if output is None and len(args) >= 5:
        output = args[4]
    if output is not None:
        destination = candidate.Path(output) / "metrics.json"
        candidate.write_json_atomic(
            destination,
            candidate.base._json_safe(metrics),
        )
    return metrics


candidate.pre_attack_value.PreAttackValueLogic = _CloseLogicFactory
candidate.Candidate07EventSignalStrategy = Candidate07SerializedEventStrategy
candidate.build_causal_signals = _build_close_signals
candidate.run_week = _run_close_week


if __name__ == "__main__":
    raise SystemExit(candidate.main())
