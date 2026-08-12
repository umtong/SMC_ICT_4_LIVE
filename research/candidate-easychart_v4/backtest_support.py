"""NautilusTrader setup, data wiring and auditable evidence output."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
import json
import math
from pathlib import Path
import re
from typing import Any

import pandas as pd

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, RiskEngineConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.objects import Currency, Money

from data import load_range, wrangler_frame

STARTING_NAV = 100_000.0
VENUE = Venue("BINANCE")
USDT = Currency.from_str("USDT")
RISK_TOLERANCE = 1.0005


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _report_with_index(frame: pd.DataFrame, fallback_name: str) -> pd.DataFrame:
    out = frame.reset_index()
    first = str(out.columns[0])
    if first == "index":
        out = out.rename(columns={out.columns[0]: fallback_name})
    return out


def _money_value(value: Any) -> float | None:
    if value is None:
        return None
    if hasattr(value, "as_double"):
        return float(value.as_double())
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", str(value))
    return float(match.group(0)) if match else None


def _money_sum(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (list, tuple)):
        return sum(item for item in (_money_value(part) for part in value) if item is not None)
    numbers = re.findall(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", str(value))
    return sum(float(item) for item in numbers)


def _tag_value(value: Any, prefix: str) -> str | None:
    if value is None:
        return None
    items = value if isinstance(value, (list, tuple, set)) else [value]
    for item in items:
        text = str(item)
        marker = text.find(prefix)
        if marker >= 0:
            suffix = text[marker + len(prefix) :]
            return re.split(r"[\s,'\"\]]", suffix, maxsplit=1)[0]
    return None


def make_engine() -> BacktestEngine:
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            trader_id=TraderId("EASYCHART-V2-001"),
            logging=LoggingConfig(log_level="ERROR"),
            risk_engine=RiskEngineConfig(bypass=False),
        ),
    )
    engine.add_venue(
        venue=VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money(STARTING_NAV, USDT)],
        base_currency=USDT,
        default_leverage=Decimal("100"),
        fill_model=FillModel(prob_fill_on_limit=1.0, prob_slippage=1.0, random_seed=42),
        bar_execution=True,
        bar_adaptive_high_low_ordering=True,
    )
    return engine


def add_symbol_data(
    engine: BacktestEngine,
    symbol: str,
    instrument: Any,
    start: date,
    end: date,
    cache: Path,
) -> tuple[BarType, BarType]:
    raw = load_range(symbol, start, end, cache)
    source_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    signal_type = BarType.from_str(f"{instrument.id}-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL")
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
    return source_type, signal_type


def final_nav(engine: BacktestEngine) -> float:
    account = engine.portfolio.account(VENUE)
    if account is None:
        raise RuntimeError("account unavailable after backtest")
    money = account.balance_total(USDT)
    if money is None:
        raise RuntimeError("USDT balance unavailable after backtest")
    return float(money.as_double())


def _write_trade_windows(strategy: Any, output: Path) -> int:
    engines_by_symbol = {state.symbol: state for state in strategy.engines.values()}
    submitted = {
        event["plan_id"]: event
        for event in strategy.event_log
        if event.get("kind") == "submitted" and event.get("plan_id")
    }
    count = 0
    with (output / "trade_windows.jsonl").open("w", encoding="utf-8") as stream:
        for plan_id, submitted_event in submitted.items():
            plan = strategy.plan_log.get(plan_id)
            if plan is None:
                continue
            state = engines_by_symbol.get(plan.symbol)
            if state is None:
                continue
            observed_index = next(
                (index for index, bar in enumerate(state.bars) if bar.ts_close_ns == plan.observed_time_ns),
                None,
            )
            if observed_index is None:
                continue
            start_index = max(0, plan.interaction_index - 24)
            end_index = min(len(state.bars), observed_index + 13)
            bars = [
                {
                    "index": index,
                    "relative_to_entry_signal": index - observed_index,
                    **asdict(state.bars[index]),
                }
                for index in range(start_index, end_index)
            ]
            related_events = [
                event
                for event in strategy.event_log
                if event.get("plan_id") == plan_id
            ]
            record = {
                "plan": asdict(plan),
                "submitted": submitted_event,
                "events": related_events,
                "bars": bars,
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
            estimated_planned_loss = quantity * estimated_per_unit_loss
            risk_utilization = (
                None if risk_budget in (None, 0) else estimated_planned_loss / float(risk_budget)
            )
            entry_slippage_ticks = adverse_entry_slippage / tick if tick > 0 else None
            row.update(
                {
                    "symbol": plan.symbol,
                    "family": plan.family.value,
                    "side": plan.side.name,
                    "signal_time_ns": plan.observed_time_ns,
                    "interaction_time_ns": plan.interaction_time_ns,
                    "confirmation_time_ns": plan.confirmation_time_ns,
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
                    "estimated_worst_case_loss_at_actual_fill": estimated_planned_loss,
                    "risk_budget_utilization": risk_utilization,
                    "entry_slippage_within_reserve": (
                        None
                        if entry_slippage_ticks is None
                        else entry_slippage_ticks <= strategy.config.estimated_entry_slippage_ticks + 1e-9
                    ),
                    "risk_budget_breach": (
                        None if risk_utilization is None else risk_utilization > RISK_TOLERANCE
                    ),
                    "source_boundary_id": plan.source_boundary_id,
                    "source_level": plan.source_level,
                    "source_span": plan.source_span,
                    "source_prominence_atr": plan.source_prominence_atr,
                    "target_boundary_id": plan.target_boundary_id,
                    "target_span": plan.target_span,
                    "target_prominence_atr": plan.target_prominence_atr,
                    "trigger_extreme": plan.trigger_extreme,
                    "origin_boundary_id": plan.origin_boundary_id,
                    "origin_level": plan.origin_level,
                },
            )
        rows.append(row)
    return pd.DataFrame(rows)


def preserve_results(
    engine: BacktestEngine,
    strategy: Any,
    output: Path,
    *,
    symbols: tuple[str, ...],
    instruments: list[Any],
    start: date,
    end: date,
    min_prominence_atr: float,
    enable_rejection: bool,
    enable_acceptance: bool,
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

    trade_windows = _write_trade_windows(strategy, output)
    trade_audit = _build_trade_audit(strategy, orders_export, positions_export, end)
    trade_audit.to_csv(output / "trade_audit.csv", index=False)

    nav = final_nav(engine)
    days = (end - start).days + 1
    # The framework report is authoritative. PositionClosed callbacks are not
    # guaranteed for the final on_stop flatten within the same engine run.
    closed = int(len(positions.index))
    event_counts = Counter(event.get("kind") for event in strategy.event_log)
    plans = int(event_counts["plan"])
    submitted_count = int(event_counts["submitted"])
    emergency = int(event_counts["emergency_exit_protective_failure"])
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
    evaluation_end_flattens = (
        int((trade_audit["exit_role"] == "EVALUATION_END_FLATTEN").sum())
        if not trade_audit.empty
        else 0
    )
    metrics = {
        "candidate": "candidate-easychart-v2",
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
        "plans": plans,
        "submitted_plans": submitted_count,
        "trade_windows": trade_windows,
        "audited_positions": int(len(trade_audit.index)),
        "orphan_audited_positions": orphan_audits,
        "missing_exit_roles": missing_exit_roles,
        "evaluation_end_flattens": evaluation_end_flattens,
        "fill_events_logged": int(event_counts["order_filled"]),
        "plans_skipped_global_slot": int(event_counts["plan_skipped_global_slot"]),
        "plans_skipped_arbitration": int(event_counts["plan_skipped_arbitration"]),
        "plans_rejected_quantity": int(event_counts["plan_rejected_quantity"]),
        "emergency_protective_exits": emergency,
        "risk_budget_breaches": risk_breaches,
        "entry_slippage_reserve_exceeds": entry_slippage_exceeds,
        "max_risk_budget_utilization": max_risk_utilization,
        "independent_trades_per_day": closed / days,
        "risk_fraction": 0.03,
        "estimated_entry_slippage_ticks": strategy.config.estimated_entry_slippage_ticks,
        "estimated_stop_slippage_ticks": strategy.config.estimated_stop_slippage_ticks,
        "minimum_gross_rr": 1.0,
        "min_prominence_atr": min_prominence_atr,
        "enable_rejection": enable_rejection,
        "enable_acceptance": enable_acceptance,
        "event_counts": dict(sorted(event_counts.items())),
        "diagnostics": {
            symbol: strategy.engines[instrument.id].diagnostics
            for symbol, instrument in zip(symbols, instruments, strict=True)
        },
    }
    write_json(output / "metrics.json", metrics)
    write_json(
        output / "run.json",
        {
            "run_id": f"ecv2-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            "candidate": "candidate-easychart-v2",
            "engine": "NautilusTrader BacktestEngine",
            "data": "Binance Vision USD-M 1m; Nautilus 5m composite signals and 1m execution",
            "contract": {
                "single_entry": True,
                "single_full_stop_market": True,
                "single_full_target": True,
                "risk_fraction_current_nav": 0.03,
                "estimated_entry_slippage_ticks": strategy.config.estimated_entry_slippage_ticks,
                "estimated_stop_slippage_ticks": strategy.config.estimated_stop_slippage_ticks,
                "min_pre_entry_gross_rr": 1.0,
                "global_pending_or_position_limit": 1,
                "partial_management": False,
                "daily_loss_limit": None,
                "trade_count_limit": None,
            },
            "evidence": {
                "client_order_id_preserved": True,
                "order_plan_tags": True,
                "fill_events": True,
                "trade_audit": True,
                "trade_windows": True,
                "evaluation_end_flatten_classified": True,
                "risk_budget_checked_at_actual_fill": True,
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
