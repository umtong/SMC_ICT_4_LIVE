"""Evidence preservation for EasyChart v5 on NautilusTrader.

The v3 report joins, risk audit and account reports are deliberately reused.
Only the trade-window writer is extended to retain the 1-minute micro trigger
frame, and stale candidate metadata is replaced after the validated report is
written.

The integrated EasyChart stream now also contains purpose-built daily/H4/local
auction setup classes.  Their zone kinds may be explicit strings and some
state machines expose a terminal reason rather than the legacy ``SetupState``
enum.  Evidence preservation accepts both representations without changing the
immutable trading decision.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import mtf_backtest_support as _base
from backtest_support import _jsonable, write_json


class _ValueString(str):
    """String-compatible audit value for legacy code expecting ``.value``."""

    @property
    def value(self) -> str:
        return str(self)


def _find_zone(scenario: Any, zone_id: str) -> Any | None:
    for detector in scenario.detectors.values():
        for zone in detector.zones:
            if zone.zone_id == zone_id:
                return zone
    return scenario.find_zone(zone_id)


def _write_v5_trade_windows(strategy: Any, output: Path) -> int:
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


def _setup_state_label(setup: Any) -> str:
    state = getattr(setup, "state", None)
    if state is not None:
        return str(getattr(state, "value", state))
    terminal = getattr(setup, "terminal_reason", None)
    if terminal:
        return f"TERMINAL:{terminal}"
    return f"ACTIVE:{type(setup).__name__}"


def _heterogeneous_scenario_diagnostics(strategy: Any) -> dict[str, Any]:
    """Serialize legacy and purpose-built auction engines in one report."""

    output: dict[str, Any] = {}
    for _, scenario in strategy.scenario_engines.items():
        setups = list(getattr(scenario, "setups", ()))
        states = Counter(_setup_state_label(setup) for setup in setups)
        detector_values: dict[str, Any] = {}
        for timeframe, detector in sorted(scenario.detectors.items(), reverse=True):
            bars = list(getattr(detector, "bars", ()))
            zones = list(getattr(detector, "zones", ()))
            active_method = getattr(detector, "active_zones", None)
            active = list(active_method()) if callable(active_method) else []
            detector_values[str(timeframe)] = {
                "bars": len(bars),
                "zones": len(zones),
                "active_zones": len(active),
                "fresh_zones": sum(
                    getattr(zone, "first_touch_index", None) is None
                    for zone in active
                ),
                "diagnostics": getattr(detector, "diagnostics", {}),
            }
        plans = list(getattr(scenario, "plans", ()))
        output[scenario.symbol] = {
            "scenario": getattr(scenario, "diagnostics", {}),
            "setups": len(setups),
            "setup_states": dict(sorted(states.items())),
            "plans": len(plans),
            "detectors": detector_values,
        }
    return output


def _legacy_kind_compatibility(strategy: Any) -> list[tuple[Any, str, Any]]:
    """Temporarily adapt explicit string kinds for the reused v3 trade audit."""

    changed: list[tuple[Any, str, Any]] = []
    for plan in strategy.plan_log.values():
        for name in (
            "higher_zone_kind",
            "lower_zone_kind",
            "target_zone_kind",
        ):
            value = getattr(plan, name)
            if hasattr(value, "value"):
                continue
            changed.append((plan, name, value))
            object.__setattr__(plan, name, _ValueString(str(value)))
    return changed


def preserve_mtf_results_v5(*args: Any, **kwargs: Any) -> dict[str, Any]:
    # The base function resolves these module globals at call time.  Inject the
    # v5 trade-window extension and heterogeneous audit compatibility without
    # copying the risk/account engine or changing a plan before execution.
    strategy = args[1] if len(args) > 1 else kwargs["strategy"]
    original_windows = _base._write_mtf_trade_windows
    original_diagnostics = _base._scenario_diagnostics
    changed = _legacy_kind_compatibility(strategy)
    _base._write_mtf_trade_windows = _write_v5_trade_windows
    _base._scenario_diagnostics = _heterogeneous_scenario_diagnostics
    try:
        metrics = _base.preserve_mtf_results(*args, **kwargs)
    finally:
        _base._write_mtf_trade_windows = original_windows
        _base._scenario_diagnostics = original_diagnostics
        for plan, name, value in changed:
            object.__setattr__(plan, name, value)

    output: Path = kwargs["output"] if "output" in kwargs else args[2]
    metrics["candidate"] = "candidate-easychart-v5-structure-first"
    metrics["decision_policy"] = (
        "structure -> objective -> interaction -> auction state -> event-local footprint -> immutable plan"
    )
    metrics["heterogeneous_auction_audit"] = True
    write_json(output / "metrics.json", metrics)
    write_json(
        output / "run.json",
        {
            "run_id": f"ecv5-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            "candidate": "candidate-easychart-v5-structure-first",
            "engine": "NautilusTrader BacktestEngine",
            "data": "Binance Vision USD-M 1m; Nautilus internal 5m/15m/60m composites",
            "scenario": (
                "causal wick structure -> pre-existing objective -> rejection/acceptance/rotation/bounce -> "
                "event-local OB/FVG where required -> first retest -> fixed entry/stop/target"
            ),
            "contract": {
                "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
                "single_global_pending_or_position": True,
                "single_entry": True,
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
            "heterogeneous_auction_audit": True,
        },
    )
    return metrics
