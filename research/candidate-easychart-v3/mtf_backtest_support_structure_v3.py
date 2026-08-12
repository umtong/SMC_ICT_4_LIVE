"""Evidence preservation for the structure-first EasyChart v3 policy."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import mtf_backtest_support as _base
from backtest_support import _jsonable, write_json


def _find_zone(scenario: Any, zone_id: str) -> Any | None:
    for detector in scenario.detectors.values():
        for zone in detector.zones:
            if zone.zone_id == zone_id:
                return zone
    return scenario.find_zone(zone_id)


def _write_structure_trade_windows(strategy: Any, output: Path) -> int:
    scenarios_by_symbol = {
        scenario.symbol: scenario
        for scenario in strategy.scenario_engines.values()
    }
    submitted = {
        event["plan_id"]: event
        for event in strategy.event_log
        if event.get("kind") == "submitted" and event.get("plan_id")
    }
    lookbacks = {60: 24, 15: 48, 5: 72, 1: 120}
    lookaheads = {60: 12, 15: 24, 5: 36, 1: 60}
    count = 0
    with (output / "mtf_trade_windows.jsonl").open("w", encoding="utf-8") as stream:
        for plan_id, submitted_event in submitted.items():
            plan = strategy.plan_log.get(plan_id)
            if plan is None:
                continue
            scenario = scenarios_by_symbol.get(plan.symbol)
            if scenario is None:
                continue
            windows: dict[str, list[dict[str, Any]]] = {}
            for timeframe, detector in sorted(scenario.detectors.items(), reverse=True):
                trigger_index = next(
                    (
                        index
                        for index, bar in enumerate(detector.bars)
                        if bar.ts_close_ns >= plan.trigger_time_ns
                    ),
                    len(detector.bars) - 1,
                )
                start_index = max(0, trigger_index - lookbacks[timeframe])
                end_index = min(len(detector.bars), trigger_index + lookaheads[timeframe] + 1)
                windows[str(timeframe)] = [
                    {
                        "index": index,
                        "relative_to_trigger": index - trigger_index,
                        **asdict(detector.bars[index]),
                    }
                    for index in range(start_index, end_index)
                ]
            zone_ids = {
                "higher": plan.higher_zone_id,
                "decision": plan.lower_zone_id,
                "trigger": plan.trigger_zone_id,
                "target": plan.target_zone_id,
            }
            zones = {
                role: None if (zone := _find_zone(scenario, zone_id)) is None else asdict(zone)
                for role, zone_id in zone_ids.items()
            }
            related_events = [
                event
                for event in strategy.event_log
                if event.get("plan_id") == plan_id
            ]
            record = {
                "plan": asdict(plan),
                "submitted": submitted_event,
                "zones": zones,
                "events": related_events,
                "bars": windows,
            }
            stream.write(json.dumps(_jsonable(record), ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def preserve_structure_results(*args: Any, **kwargs: Any) -> dict[str, Any]:
    # Reuse the validated joins, risk audit and Nautilus account reports.  Only
    # the evidence window writer needs the one-minute micro decision frame.
    original = _base._write_mtf_trade_windows
    _base._write_mtf_trade_windows = _write_structure_trade_windows
    try:
        metrics = _base.preserve_mtf_results(*args, **kwargs)
    finally:
        _base._write_mtf_trade_windows = original

    output: Path = kwargs["output"] if "output" in kwargs else args[2]
    metrics["candidate"] = "candidate-easychart-v3-structure-first"
    metrics["decision_policy"] = (
        "structure -> objective -> interaction -> auction state -> "
        "event-local footprint -> immutable plan"
    )
    metrics["entry_policy"] = "first confirmed retest close -> one market parent"
    write_json(output / "metrics.json", metrics)
    write_json(
        output / "run.json",
        {
            "run_id": f"ecv3-structure-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            "candidate": "candidate-easychart-v3-structure-first",
            "engine": "NautilusTrader BacktestEngine",
            "data": "Binance Vision USD-M 1m; Nautilus internal 5m/15m/60m composites",
            "scenario": (
                "causal wick structure -> pre-existing objective -> "
                "rejection/acceptance/rotation/bounce -> event-local OB/FVG "
                "where required -> first retest -> fixed entry/stop/target"
            ),
            "contract": {
                "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
                "single_global_pending_or_position": True,
                "single_entry_decision": True,
                "single_full_stop_market": True,
                "single_full_target": True,
                "risk_fraction_current_nav": 0.03,
                "min_pre_entry_gross_rr": 1.0,
                "partial_management": False,
                "daily_loss_limit": False,
                "daily_trade_limit": False,
            },
            "provenance_classes": [
                "SOURCE_EXPLICIT",
                "SOURCE_AMBIGUITY_TRANSLATION",
                "RESEARCH_HYPOTHESIS",
                "EXTERNAL_METHOD",
            ],
        },
    )
    return metrics
