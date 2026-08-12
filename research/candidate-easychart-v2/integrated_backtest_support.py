"""Minimal evidence output for the unified EasyChart Nautilus strategy."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from enum import Enum
import json
from pathlib import Path
from typing import Any

import pandas as pd

from nautilus_trader.backtest.engine import BacktestEngine

from backtest_support import (
    RISK_TOLERANCE,
    STARTING_NAV,
    VENUE,
    _jsonable,
    _money_sum,
    _money_value,
    _report_with_index,
    _tag_value,
    final_nav,
    write_json,
)


def _family(plan: Any) -> str:
    value = getattr(plan, "family", type(plan).__name__)
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _entry_kind(plan: Any) -> str:
    return str(getattr(plan, "entry_order_kind", "MARKET")).upper()


def _instrument_id_for_symbol(strategy: Any, symbol: str) -> Any | None:
    for instrument_id, instrument in strategy.instruments.items():
        if instrument.raw_symbol.value == symbol:
            return instrument_id
    return None


def _bars_for_plan(strategy: Any, plan: Any) -> dict[int, list[Any]]:
    instrument_id = _instrument_id_for_symbol(strategy, plan.symbol)
    if instrument_id is None:
        return {}
    family = _family(plan)
    if family == "MTF_ZONE_SWEEP_FIRST_5M_OB":
        engine = strategy.mtf_sweep_engines[instrument_id]
        return {timeframe: detector.bars for timeframe, detector in engine.detectors.items()}
    if family == "MTF_OB_OVERLAP_FIRST_TOUCH":
        engine = strategy.mtf_touch_engines[instrument_id]
        return {timeframe: detector.bars for timeframe, detector in engine.detectors.items()}
    if family == "TRENDLINE_BREAK_FIRST_RETEST_OB":
        engine = strategy.trendline_engines[instrument_id]
        return {engine.timeframe_minutes: engine.line_tracker.bars}
    return {}


def _write_trade_windows(strategy: Any, output: Path) -> int:
    submitted = {
        event["plan_id"]: event
        for event in strategy.event_log
        if event.get("kind") == "submitted" and event.get("plan_id")
    }
    count = 0
    with (output / "trade_windows.jsonl").open("w", encoding="utf-8") as stream:
        for plan_id, submit_event in submitted.items():
            plan = strategy.plan_log.get(plan_id)
            if plan is None:
                continue
            windows: dict[str, list[dict[str, Any]]] = {}
            for timeframe, bars in sorted(_bars_for_plan(strategy, plan).items(), reverse=True):
                if not bars:
                    continue
                signal_index = next(
                    (index for index, bar in enumerate(bars) if bar.ts_close_ns >= plan.observed_time_ns),
                    len(bars) - 1,
                )
                lookback = 24 if timeframe >= 60 else 48 if timeframe >= 15 else 72
                lookahead = 12 if timeframe >= 60 else 24 if timeframe >= 15 else 36
                start = max(0, signal_index - lookback)
                end = min(len(bars), signal_index + lookahead + 1)
                windows[str(timeframe)] = [
                    {
                        "index": index,
                        "relative_to_signal": index - signal_index,
                        **asdict(bars[index]),
                    }
                    for index in range(start, end)
                ]
            related_events = [
                event for event in strategy.event_log if event.get("plan_id") == plan_id
            ]
            record = {
                "plan": asdict(plan),
                "submitted": submit_event,
                "events": related_events,
                "bars": windows,
            }
            stream.write(json.dumps(_jsonable(record), ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def _build_trade_audit(
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
        instrument.raw_symbol.value: instrument for instrument in strategy.instruments.values()
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
        submit = {} if plan_id is None else submitted.get(plan_id, {})
        realized_pnl = _money_value(position.get("realized_pnl"))
        risk_budget = submit.get("risk_budget")
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
            "nav_at_submission": submit.get("nav_at_submission"),
            "risk_budget": risk_budget,
            "actual_net_r": (
                None if realized_pnl is None or risk_budget in (None, 0)
                else realized_pnl / float(risk_budget)
            ),
            "entry_order_type": None if opening_order is None else opening_order.get("type"),
            "entry_liquidity_side": None if opening_order is None else opening_order.get("liquidity_side"),
            "exit_role": exit_role,
            "exit_order_type": None if closing_order is None else closing_order.get("type"),
            "exit_liquidity_side": None if closing_order is None else closing_order.get("liquidity_side"),
        }
        if plan is not None:
            instrument = instruments_by_symbol.get(plan.symbol)
            if instrument is None:
                raise RuntimeError(f"instrument unavailable for audit: {plan.symbol}")
            tick = float(instrument.price_increment)
            direction = float(plan.side.value)
            signed_entry_slippage = direction * (actual_entry - float(plan.entry))
            adverse_entry_slippage = max(0.0, signed_entry_slippage)
            allowed_entry_ticks = 0 if _entry_kind(plan) == "LIMIT" else strategy.config.estimated_entry_slippage_ticks
            stop_reserve = tick * strategy.config.estimated_stop_slippage_ticks
            worst_stop_fill = float(plan.stop) - direction * stop_reserve
            per_unit_loss = abs(actual_entry - worst_stop_fill)
            per_unit_loss += actual_entry * float(strategy.config.estimated_entry_fee_rate)
            per_unit_loss += worst_stop_fill * float(strategy.config.estimated_stop_fee_rate)
            per_unit_loss += actual_entry * float(strategy.config.estimated_funding_rate)
            estimated_loss = quantity * per_unit_loss
            utilization = None if risk_budget in (None, 0) else estimated_loss / float(risk_budget)
            row.update(
                {
                    "symbol": plan.symbol,
                    "family": _family(plan),
                    "side": plan.side.name,
                    "entry_order_kind": _entry_kind(plan),
                    "signal_time_ns": plan.observed_time_ns,
                    "planned_entry": plan.entry,
                    "planned_stop": plan.stop,
                    "planned_target": plan.target,
                    "planned_gross_rr": plan.gross_rr,
                    "signed_entry_slippage": signed_entry_slippage,
                    "adverse_entry_slippage": adverse_entry_slippage,
                    "adverse_entry_slippage_ticks": adverse_entry_slippage / tick if tick else None,
                    "allowed_entry_slippage_ticks": allowed_entry_ticks,
                    "entry_slippage_within_reserve": (
                        adverse_entry_slippage <= tick * allowed_entry_ticks + 1e-9
                    ),
                    "worst_stop_fill": worst_stop_fill,
                    "estimated_worst_case_loss_at_actual_fill": estimated_loss,
                    "risk_budget_utilization": utilization,
                    "risk_budget_breach": (
                        None if utilization is None else utilization > RISK_TOLERANCE
                    ),
                    "plan_lineage_json": json.dumps(
                        _jsonable(asdict(plan)),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                },
            )
        rows.append(row)
    return pd.DataFrame(rows)


def preserve_integrated_results(
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
    account = engine.trader.generate_account_report(VENUE)
    fills_export = _report_with_index(fills, "client_order_id")
    orders_export = _report_with_index(orders, "client_order_id")
    positions_export = _report_with_index(positions, "position_id")
    account_export = _report_with_index(account, "ts_event")
    fills_export.to_csv(output / "fills.csv", index=False)
    orders_export.to_csv(output / "orders.csv", index=False)
    positions_export.to_csv(output / "positions.csv", index=False)
    account_export.to_csv(output / "account.csv", index=False)

    with (output / "scenario_events.jsonl").open("w", encoding="utf-8") as stream:
        for event in strategy.event_log:
            stream.write(json.dumps(_jsonable(event), ensure_ascii=False, sort_keys=True) + "\n")
    pd.DataFrame(strategy.event_log).to_csv(output / "decision_events.csv", index=False)
    with (output / "plans.jsonl").open("w", encoding="utf-8") as stream:
        for plan in strategy.plan_log.values():
            stream.write(json.dumps(_jsonable(asdict(plan)), ensure_ascii=False, sort_keys=True) + "\n")

    trade_windows = _write_trade_windows(strategy, output)
    trade_audit = _build_trade_audit(strategy, orders_export, positions_export, end)
    trade_audit.to_csv(output / "trade_audit.csv", index=False)

    nav = final_nav(engine)
    days = (end - start).days + 1
    event_counts = Counter(event.get("kind") for event in strategy.event_log)
    plan_family_counts = Counter(
        str(event.get("family"))
        for event in strategy.event_log
        if event.get("kind") == "plan"
    )
    family_closed: dict[str, dict[str, Any]] = {}
    if not trade_audit.empty and "family" in trade_audit.columns:
        for family, frame in trade_audit.dropna(subset=["family"]).groupby("family"):
            pnl = pd.to_numeric(frame["realized_pnl"], errors="coerce")
            net_r = pd.to_numeric(frame["actual_net_r"], errors="coerce")
            family_closed[str(family)] = {
                "closed_positions": int(len(frame.index)),
                "realized_pnl": float(pnl.sum()),
                "mean_net_r": None if net_r.dropna().empty else float(net_r.mean()),
                "positive_positions": int((pnl > 0).sum()),
            }

    orphan = int(trade_audit["plan_id"].isna().sum()) if not trade_audit.empty else 0
    missing_exit = int(trade_audit["exit_role"].isna().sum()) if not trade_audit.empty else 0
    risk_breaches = (
        int(trade_audit["risk_budget_breach"].fillna(False).astype(bool).sum())
        if "risk_budget_breach" in trade_audit.columns else 0
    )
    slippage_exceeds = (
        int((~trade_audit["entry_slippage_within_reserve"].fillna(True).astype(bool)).sum())
        if "entry_slippage_within_reserve" in trade_audit.columns else 0
    )
    emergency = int(event_counts["emergency_exit_protective_failure"])
    closed = int(len(positions.index))
    metrics = {
        "candidate": "candidate-easychart-v2-integrated",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "symbols": list(symbols),
        "starting_nav": STARTING_NAV,
        "final_nav": nav,
        "total_return": nav / STARTING_NAV - 1.0,
        "daily_geometric_growth": (nav / STARTING_NAV) ** (1.0 / days) - 1.0 if nav > 0 else -1.0,
        "calendar_days": days,
        "closed_positions": closed,
        "independent_trades_per_day": closed / days,
        "fills": int(len(fills.index)),
        "plans": int(event_counts["plan"]),
        "submitted_plans": int(event_counts["submitted"]),
        "trade_windows": trade_windows,
        "plans_by_family": dict(sorted(plan_family_counts.items())),
        "closed_by_family": family_closed,
        "plans_skipped_global_slot": int(event_counts["plan_skipped_global_slot"]),
        "plans_skipped_arbitration": int(event_counts["plan_skipped_arbitration"]),
        "pending_limit_cancellations": int(event_counts["pending_limit_cancel_requested"]),
        "orphan_audited_positions": orphan,
        "missing_exit_roles": missing_exit,
        "risk_budget_breaches": risk_breaches,
        "entry_slippage_reserve_exceeds": slippage_exceeds,
        "emergency_protective_exits": emergency,
        "event_counts": dict(sorted(event_counts.items())),
        "diagnostics": strategy.diagnostics(),
    }
    write_json(output / "metrics.json", metrics)
    write_json(
        output / "run.json",
        {
            "run_id": f"ecv2-integrated-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            "engine": "NautilusTrader BacktestEngine",
            "data": "Binance Vision USD-M 1m; Nautilus 5m/15m/1h internal bars",
            "families": [
                "MTF_ZONE_SWEEP_FIRST_5M_OB",
                "MTF_OB_OVERLAP_FIRST_TOUCH",
                "TRENDLINE_BREAK_FIRST_RETEST_OB",
            ],
            "contract": {
                "single_account": True,
                "global_pending_or_position_limit": 1,
                "risk_fraction_current_nav": 0.03,
                "single_entry": True,
                "single_full_stop_market": True,
                "single_full_target": True,
                "min_pre_entry_gross_rr": 1.0,
                "partial_management": False,
                "daily_loss_limit": None,
                "trade_count_limit": None,
            },
        },
    )

    failures: list[str] = []
    if orphan:
        failures.append(f"{orphan} positions could not be joined to a plan")
    if missing_exit:
        failures.append(f"{missing_exit} exits lack a classified role")
    if risk_breaches:
        failures.append(f"{risk_breaches} positions exceed the planned 3% loss budget")
    if slippage_exceeds:
        failures.append(f"{slippage_exceeds} entries exceed their pre-entry reserve")
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
