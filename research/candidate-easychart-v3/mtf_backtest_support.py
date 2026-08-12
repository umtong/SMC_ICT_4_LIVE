"""NautilusTrader data wiring and evidence for the EasyChart MTF scenario."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.data import Bar, BarType

from backtest_support import (
    RISK_TOLERANCE,
    STARTING_NAV,
    _jsonable,
    _money_sum,
    _money_value,
    _report_with_index,
    _tag_value,
    final_nav,
    write_json,
)
from data import load_range, wrangler_frame


def add_symbol_mtf_data(
    engine: BacktestEngine,
    symbol: str,
    instrument: Any,
    start: date,
    end: date,
    cache: Path,
) -> tuple[BarType, BarType, BarType, BarType]:
    """Add one-minute execution data and return 1m/5m/15m/60m bar types."""
    raw = load_range(symbol, start, end, cache)
    source_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    trigger_type = BarType.from_str(f"{instrument.id}-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL")
    decision_type = BarType.from_str(f"{instrument.id}-15-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL")
    higher_type = BarType.from_str(f"{instrument.id}-60-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL")
    frame = wrangler_frame(raw, 1)
    source = [
        Bar(
            bar_type=source_type,
            open=instrument.make_price(row.open),
            high=instrument.make_price(row.high),
            low=instrument.make_price(row.low),
            close=instrument.make_price(row.close),
            volume=instrument.make_qty(row.volume),
            ts_event=int(row.Index.value),
            ts_init=int(row.Index.value),
        )
        for row in frame.itertuples()
    ]
    engine.add_data(source, sort=False)
    return source_type, trigger_type, decision_type, higher_type


def _find_zone(scenario: Any, zone_id: str) -> Any | None:
    for detector in scenario.detectors.values():
        for zone in detector.zones:
            if zone.zone_id == zone_id:
                return zone
    return None


def _write_mtf_trade_windows(strategy: Any, output: Path) -> int:
    scenarios_by_symbol = {
        scenario.symbol: scenario
        for scenario in strategy.scenario_engines.values()
    }
    submitted = {
        event["plan_id"]: event
        for event in strategy.event_log
        if event.get("kind") == "submitted" and event.get("plan_id")
    }
    lookbacks = {60: 24, 15: 48, 5: 72}
    lookaheads = {60: 12, 15: 24, 5: 36}
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
            stream.write(
                __import__("json").dumps(_jsonable(record), ensure_ascii=False, sort_keys=True) + "\n",
            )
            count += 1
    return count


def _build_mtf_trade_audit(
    strategy: Any,
    orders_export: pd.DataFrame,
    positions_export: pd.DataFrame,
    evaluation_end: date,
) -> pd.DataFrame:
    if "client_order_id" not in orders_export.columns:
        raise RuntimeError("orders report lost client_order_id index")
    orders = orders_export.copy()
    orders["client_order_id"] = orders["client_order_id"].astype(str)
    orders_by_id = orders.set_index("client_order_id", drop=False)
    submitted = {
        event["plan_id"]: event
        for event in strategy.event_log
        if event.get("kind") == "submitted" and event.get("plan_id")
    }
    instruments_by_symbol = {
        instrument.raw_symbol.value: instrument
        for instrument in strategy.instruments.values()
    }
    evaluation_flatten_ts = pd.Timestamp(evaluation_end + timedelta(days=1), tz="UTC")
    rows: list[dict[str, Any]] = []
    for _, position in positions_export.iterrows():
        opening_id = str(position.get("opening_order_id"))
        closing_id = str(position.get("closing_order_id"))
        opening_order = orders_by_id.loc[opening_id] if opening_id in orders_by_id.index else None
        closing_order = orders_by_id.loc[closing_id] if closing_id in orders_by_id.index else None
        if isinstance(opening_order, pd.DataFrame):
            opening_order = opening_order.iloc[0]
        if isinstance(closing_order, pd.DataFrame):
            closing_order = closing_order.iloc[0]
        plan_id = None if opening_order is None else _tag_value(opening_order.get("tags"), "PLAN:")
        plan = None if plan_id is None else strategy.plan_log.get(plan_id)
        submit_event = {} if plan_id is None else submitted.get(plan_id, {})
        realized_pnl = _money_value(position.get("realized_pnl"))
        nav_at_submission = submit_event.get("nav_at_submission")
        risk_budget = submit_event.get("risk_budget")
        actual_entry = float(position.get("avg_px_open"))
        actual_exit = float(position.get("avg_px_close"))
        quantity = float(position.get("peak_qty"))
        close_ts = pd.to_datetime(position.get("ts_closed"), utc=True, errors="coerce")
        exit_role = None if closing_order is None else _tag_value(closing_order.get("tags"), "ROLE:")
        if exit_role is None and close_ts == evaluation_flatten_ts:
            exit_role = "EVALUATION_END_FLATTEN"
        row: dict[str, Any] = {
            "position_id": str(position.get("position_id")),
            "plan_id": plan_id,
            "opening_order_id": opening_id,
            "closing_order_id": closing_id,
            "instrument_id": str(position.get("instrument_id")),
            "ts_opened": position.get("ts_opened"),
            "ts_closed": position.get("ts_closed"),
            "duration_ns": position.get("duration_ns"),
            "quantity": quantity,
            "actual_entry": actual_entry,
            "actual_exit": actual_exit,
            "realized_return": position.get("realized_return"),
            "realized_pnl": realized_pnl,
            "commissions": _money_sum(position.get("commissions")),
            "nav_at_submission": nav_at_submission,
            "risk_budget": risk_budget,
            "actual_net_r": (
                None
                if realized_pnl is None or risk_budget in (None, 0)
                else realized_pnl / float(risk_budget)
            ),
            "entry_order_type": None if opening_order is None else opening_order.get("type"),
            "entry_liquidity_side": None if opening_order is None else opening_order.get("liquidity_side"),
            "entry_slippage_reported": None if opening_order is None else opening_order.get("slippage"),
            "exit_role": exit_role,
            "exit_order_type": None if closing_order is None else closing_order.get("type"),
            "exit_liquidity_side": None if closing_order is None else closing_order.get("liquidity_side"),
            "exit_slippage_reported": None if closing_order is None else closing_order.get("slippage"),
        }
        if plan is not None:
            instrument = instruments_by_symbol.get(plan.symbol)
            if instrument is None:
                raise RuntimeError(f"instrument unavailable for audit: {plan.symbol}")
            tick = float(instrument.price_increment)
            signed_entry_slippage = float(plan.side.value) * (actual_entry - plan.entry)
            adverse_entry_slippage = max(0.0, signed_entry_slippage)
            entry_slippage_reserve = tick * strategy.config.estimated_entry_slippage_ticks
            stop_slippage_reserve = tick * strategy.config.estimated_stop_slippage_ticks
            worst_stop_fill = (
                plan.stop - stop_slippage_reserve
                if plan.side.value > 0
                else plan.stop + stop_slippage_reserve
            )
            worst_stop_distance = abs(actual_entry - worst_stop_fill)
            actual_gross_rr = (
                None if worst_stop_distance <= 0 else abs(plan.target - actual_entry) / worst_stop_distance
            )
            estimated_per_unit_loss = worst_stop_distance
            estimated_per_unit_loss += actual_entry * float(strategy.config.estimated_entry_fee_rate)
            estimated_per_unit_loss += worst_stop_fill * float(strategy.config.estimated_stop_fee_rate)
            estimated_per_unit_loss += actual_entry * float(strategy.config.estimated_funding_rate)
            estimated_worst_loss = quantity * estimated_per_unit_loss
            risk_utilization = (
                None if risk_budget in (None, 0) else estimated_worst_loss / float(risk_budget)
            )
            entry_slippage_ticks = adverse_entry_slippage / tick if tick > 0 else None
            opened_ts = pd.to_datetime(position.get("ts_opened"), utc=True, errors="coerce")
            signal_ts = pd.Timestamp(plan.observed_time_ns, unit="ns", tz="UTC")
            entry_delay_ns = None if pd.isna(opened_ts) else int((opened_ts - signal_ts).value)
            row.update(
                {
                    "symbol": plan.symbol,
                    "family": plan.family,
                    "side": plan.side.name,
                    "signal_time_ns": plan.observed_time_ns,
                    "interaction_time_ns": plan.interaction_time_ns,
                    "trigger_time_ns": plan.trigger_time_ns,
                    "interaction_to_trigger_ns": plan.trigger_time_ns - plan.interaction_time_ns,
                    "entry_delay_ns": entry_delay_ns,
                    "planned_entry": plan.entry,
                    "planned_stop": plan.stop,
                    "planned_target": plan.target,
                    "planned_gross_rr": plan.gross_rr,
                    "actual_gross_rr_after_reserve": actual_gross_rr,
                    "signed_entry_slippage": signed_entry_slippage,
                    "adverse_entry_slippage": adverse_entry_slippage,
                    "adverse_entry_slippage_ticks": entry_slippage_ticks,
                    "entry_slippage_reserve": entry_slippage_reserve,
                    "stop_slippage_reserve": stop_slippage_reserve,
                    "worst_stop_fill": worst_stop_fill,
                    "estimated_worst_case_loss_at_actual_fill": estimated_worst_loss,
                    "risk_budget_utilization": risk_utilization,
                    "entry_slippage_within_reserve": (
                        None
                        if entry_slippage_ticks is None
                        else entry_slippage_ticks <= strategy.config.estimated_entry_slippage_ticks + 1e-9
                    ),
                    "risk_budget_breach": (
                        None if risk_utilization is None else risk_utilization > RISK_TOLERANCE
                    ),
                    "setup_id": plan.setup_id,
                    "higher_zone_id": plan.higher_zone_id,
                    "higher_zone_kind": plan.higher_zone_kind.value,
                    "higher_strength_ratio": plan.higher_strength_ratio,
                    "decision_zone_id": plan.lower_zone_id,
                    "decision_zone_kind": plan.lower_zone_kind.value,
                    "decision_strength_ratio": plan.lower_strength_ratio,
                    "trigger_zone_id": plan.trigger_zone_id,
                    "trigger_strength_ratio": plan.trigger_strength_ratio,
                    "target_zone_id": plan.target_zone_id,
                    "target_zone_kind": plan.target_zone_kind.value,
                    "context_kind_diversity": len({plan.higher_zone_kind, plan.lower_zone_kind}),
                    "overlap_lower": plan.overlap_lower,
                    "overlap_upper": plan.overlap_upper,
                },
            )
        rows.append(row)
    return pd.DataFrame(rows)


def _scenario_diagnostics(strategy: Any) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for instrument_id, scenario in strategy.scenario_engines.items():
        states = Counter(setup.state.value for setup in scenario.setups)
        detector_values: dict[str, Any] = {}
        for timeframe, detector in sorted(scenario.detectors.items(), reverse=True):
            detector_values[str(timeframe)] = {
                "bars": len(detector.bars),
                "zones": len(detector.zones),
                "active_zones": len(detector.active_zones()),
                "fresh_zones": sum(zone.first_touch_index is None for zone in detector.active_zones()),
                "diagnostics": detector.diagnostics,
            }
        output[scenario.symbol] = {
            "scenario": scenario.diagnostics,
            "setups": len(scenario.setups),
            "setup_states": dict(sorted(states.items())),
            "plans": len(scenario.plans),
            "detectors": detector_values,
        }
    return output


def preserve_mtf_results(
    engine: BacktestEngine,
    strategy: Any,
    output: Path,
    *,
    symbols: tuple[str, ...],
    start: date,
    end: date,
) -> dict[str, Any]:
    fills = engine.trader.generate_order_fills_report()
    orders = engine.trader.generate_orders_report()
    positions = engine.trader.generate_positions_report()
    account = engine.trader.generate_account_report(__import__("backtest_support").VENUE)
    fills_export = _report_with_index(fills, "client_order_id")
    orders_export = _report_with_index(orders, "client_order_id")
    positions_export = _report_with_index(positions, "position_id")
    account_export = _report_with_index(account, "ts_event")
    fills_export.to_csv(output / "fills.csv", index=False)
    orders_export.to_csv(output / "orders.csv", index=False)
    positions_export.to_csv(output / "positions.csv", index=False)
    account_export.to_csv(output / "account.csv", index=False)

    import json

    with (output / "scenario_events.jsonl").open("w", encoding="utf-8") as stream:
        for event in strategy.event_log:
            stream.write(json.dumps(_jsonable(event), ensure_ascii=False, sort_keys=True) + "\n")
    pd.DataFrame(strategy.event_log).to_csv(output / "decision_events.csv", index=False)

    trade_windows = _write_mtf_trade_windows(strategy, output)
    trade_audit = _build_mtf_trade_audit(strategy, orders_export, positions_export, end)
    trade_audit.to_csv(output / "trade_audit.csv", index=False)

    nav = final_nav(engine)
    days = (end - start).days + 1
    closed = int(len(positions.index))
    event_counts = Counter(event.get("kind") for event in strategy.event_log)
    orphan_audits = int(trade_audit["plan_id"].isna().sum()) if not trade_audit.empty else 0
    missing_exit_roles = int(trade_audit["exit_role"].isna().sum()) if not trade_audit.empty else 0
    risk_breaches = (
        int(trade_audit["risk_budget_breach"].fillna(False).astype(bool).sum())
        if "risk_budget_breach" in trade_audit.columns
        else 0
    )
    entry_slippage_exceeds = (
        int((~trade_audit["entry_slippage_within_reserve"].fillna(True).astype(bool)).sum())
        if "entry_slippage_within_reserve" in trade_audit.columns
        else 0
    )
    max_risk_utilization = (
        float(trade_audit["risk_budget_utilization"].max())
        if "risk_budget_utilization" in trade_audit.columns and not trade_audit.empty
        else None
    )
    emergency = int(event_counts["emergency_exit_protective_failure"])
    metrics = {
        "candidate": "candidate-easychart-v2-mtf-overlap",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "symbols": list(symbols),
        "starting_nav": STARTING_NAV,
        "final_nav": nav,
        "total_return": nav / STARTING_NAV - 1.0,
        "daily_geometric_growth": (nav / STARTING_NAV) ** (1.0 / days) - 1.0 if nav > 0 else -1.0,
        "calendar_days": days,
        "fills": int(len(fills.index)),
        "closed_positions": closed,
        "plans": int(event_counts["plan"]),
        "submitted_plans": int(event_counts["submitted"]),
        "trade_windows": trade_windows,
        "audited_positions": int(len(trade_audit.index)),
        "orphan_audited_positions": orphan_audits,
        "missing_exit_roles": missing_exit_roles,
        "risk_budget_breaches": risk_breaches,
        "entry_slippage_reserve_exceeds": entry_slippage_exceeds,
        "max_risk_budget_utilization": max_risk_utilization,
        "emergency_protective_exits": emergency,
        "plans_skipped_global_slot": int(event_counts["plan_skipped_global_slot"]),
        "plans_skipped_arbitration": int(event_counts["plan_skipped_arbitration"]),
        "plans_rejected_quantity": int(event_counts["plan_rejected_quantity"]),
        "independent_trades_per_day": closed / days,
        "risk_fraction": strategy.config.risk_fraction,
        "estimated_entry_slippage_ticks": strategy.config.estimated_entry_slippage_ticks,
        "estimated_stop_slippage_ticks": strategy.config.estimated_stop_slippage_ticks,
        "minimum_gross_rr": strategy.config.min_gross_rr,
        "event_counts": dict(sorted(event_counts.items())),
        "diagnostics": _scenario_diagnostics(strategy),
    }
    write_json(output / "metrics.json", metrics)
    write_json(
        output / "run.json",
        {
            "run_id": f"ecv2-mtf-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            "candidate": "candidate-easychart-v2-mtf-overlap",
            "engine": "NautilusTrader BacktestEngine",
            "data": "Binance Vision USD-M 1m; Nautilus 5m/15m/60m internal composite bars",
            "scenario": (
                "60m OB/FVG context zone -> 15m same-side overlap -> first interaction -> "
                "5m engulfing OB confirmation -> pre-existing unspent opposite OB/FVG target"
            ),
            "contract": {
                "single_entry": True,
                "single_full_stop_market": True,
                "single_full_target": True,
                "risk_fraction_current_nav": strategy.config.risk_fraction,
                "estimated_entry_slippage_ticks": strategy.config.estimated_entry_slippage_ticks,
                "estimated_stop_slippage_ticks": strategy.config.estimated_stop_slippage_ticks,
                "min_pre_entry_gross_rr": strategy.config.min_gross_rr,
                "global_pending_or_position_limit": 1,
                "partial_management": False,
            },
        },
    )

    failures: list[str] = []
    if orphan_audits:
        failures.append(f"{orphan_audits} positions could not be joined to a plan")
    if missing_exit_roles:
        failures.append(f"{missing_exit_roles} exits lack a classified role")
    if risk_breaches:
        failures.append(f"{risk_breaches} positions exceed the three-percent planned loss budget")
    if entry_slippage_exceeds:
        failures.append(f"{entry_slippage_exceeds} entries exceed the calibrated slippage reserve")
    if emergency:
        failures.append(f"{emergency} emergency exits followed protective-order failure")
    validation = {
        "status": "FAIL" if failures else "PASS",
        "risk_tolerance": RISK_TOLERANCE,
        "failures": failures,
    }
    write_json(output / "validation.json", validation)
    if failures:
        raise RuntimeError("; ".join(failures))
    return metrics
