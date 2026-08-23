"""Descriptive evidence derived from native Nautilus replay reports.

Position rows are joined to immutable plan intent only by the native
``opening_order_id`` / durable ``client_order_id`` key.  No price/time fuzzy
matching is permitted.  Missing causal evidence remains explicitly unknown.

NautilusTrader 1.230's account report exposes native account totals but not a
continuous mark-to-market equity series.  Daily values and drawdown therefore
carry an explicit ``NATIVE_ACCOUNT_TOTAL`` basis; unrealized PnL is not
silently reconstructed from bars.
"""
from __future__ import annotations

from contextlib import closing
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable, Mapping, Sequence

from .domain import SYMBOLS


UTC = timezone.utc
DAY_NS = 86_400_000_000_000
MINUTE_NS = 60_000_000_000
NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def _missing(value: Any) -> bool:
    if value is None:
        return True
    if str(value) in {"<NA>", "NaT", "nan", "None"}:
        return True
    try:
        return bool(math.isnan(float(value)))
    except (TypeError, ValueError):
        return False


def _number(value: Any) -> float | None:
    if _missing(value):
        return None
    if hasattr(value, "as_double"):
        try:
            result = float(value.as_double())
            return result if math.isfinite(result) else None
        except (TypeError, ValueError):
            return None
    if isinstance(value, str):
        match = NUMBER.search(value.replace(",", ""))
        if match is None:
            return None
        value = match.group(0)
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _money_total(value: Any) -> float | None:
    if _missing(value):
        return None
    if isinstance(value, (list, tuple)):
        amounts = [_number(item) for item in value]
        usable = [item for item in amounts if item is not None]
        return sum(usable) if len(usable) == len(amounts) else None
    return _number(value)


def _decimal_number(value: Any) -> Decimal | None:
    if _missing(value):
        return None
    if hasattr(value, "as_decimal"):
        try:
            result = value.as_decimal()
            return result if result.is_finite() else None
        except (TypeError, ValueError):
            return None
    if isinstance(value, str):
        match = NUMBER.search(value.replace(",", ""))
        if match is None:
            return None
        value = match.group(0)
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _time_ns(value: Any) -> int | None:
    if _missing(value):
        return None
    possible = getattr(value, "value", None)
    if isinstance(possible, int) and possible > -(2**63):
        return possible
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=UTC)
        return int(aware.timestamp() * 1_000_000_000)
    if isinstance(value, str) and not value.strip().lstrip("+-").isdigit():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        aware = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        return int(aware.timestamp() * 1_000_000_000)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _records(report: Any) -> list[dict[str, Any]]:
    if report is None:
        return []
    reset = getattr(report, "reset_index", None)
    if callable(reset):
        table = reset()
        converted = table.to_dict(orient="records")
        return [dict(item) for item in converted]
    if isinstance(report, Mapping):
        return [dict(report)]
    return [dict(item) for item in report]


def _position_id(row: Mapping[str, Any]) -> str | None:
    for name in ("position_id", "index"):
        if not _missing(row.get(name)):
            return str(row[name])
    return None


def _symbol(value: Any) -> str | None:
    if _missing(value):
        return None
    return re.split(r"[-.]", str(value), maxsplit=1)[0].upper()


def _load_parent_evidence(
    state_path: str | Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, str], int]:
    path = Path(state_path)
    if not path.is_file():
        return {}, {}, 0
    with closing(sqlite3.connect(path)) as connection:
        rows = connection.execute(
            "SELECT event_type, payload_json FROM runtime_events ORDER BY sequence",
        ).fetchall()
    parents: dict[str, dict[str, Any]] = {}
    parent_sources: dict[str, str] = {}
    order_roles: dict[str, str] = {}
    for event_type, payload_json in rows:
        payload = json.loads(payload_json)
        if event_type == "ORDER_ACCEPTED":
            order_id = payload.get("client_order_id")
            role = str(payload.get("role") or "").upper()
            if order_id and role:
                prior = order_roles.get(str(order_id))
                if prior is not None and prior != role:
                    raise RuntimeError(f"conflicting accepted-order role for {order_id}")
                order_roles[str(order_id)] = role
            continue
        if event_type not in {"PARENT_ORDER_SUBMITTED", "PARENT_LIMIT_SUBMITTED"}:
            continue
        order_id = payload.get("client_order_id")
        if not order_id:
            continue
        key = str(order_id)
        existing = parents.get(key)
        if existing is not None:
            immutable_fields = ("plan", "sizing", "quantity")
            if any(existing.get(name) != payload.get(name) for name in immutable_fields):
                raise RuntimeError(f"conflicting parent evidence for {order_id}")
            # The generic event covers both response-market and first-return
            # limit parents.  Keep it when the legacy limit-specific companion
            # follows, while retaining the latter as a backward-compatible
            # fallback for older databases.
            if parent_sources[key] == "PARENT_ORDER_SUBMITTED":
                continue
        parents[key] = payload
        parent_sources[key] = event_type
    return parents, order_roles, len(rows)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _chart_coverage(
    connection: sqlite3.Connection,
    *,
    symbol: str,
    start_time_ns: int,
    end_time_ns: int,
) -> dict[str, Any]:
    first_open = ((start_time_ns + MINUTE_NS - 1) // MINUTE_NS) * MINUTE_NS
    last_open = ((end_time_ns - MINUTE_NS) // MINUTE_NS) * MINUTE_NS
    expected = 0 if last_open < first_open else (last_open - first_open) // MINUTE_NS + 1
    row = connection.execute(
        "SELECT COUNT(*) AS bar_count, MIN(open_time_ns) AS first_open, "
        "MAX(close_time_ns) AS last_close "
        "FROM bars WHERE symbol=? AND interval_minutes=1 "
        "AND open_time_ns>=? AND close_time_ns<=?",
        (symbol, start_time_ns, end_time_ns),
    ).fetchone()
    observed = int(row[0]) if row is not None else 0
    return {
        "chart_expected_bar_count": int(expected),
        "chart_observed_bar_count": observed,
        "chart_first_open_time_ns": None if row is None else row[1],
        "chart_last_close_time_ns": None if row is None else row[2],
        "chart_coverage_status": (
            "EXACT" if observed == expected else "MISSING_BAR_COVERAGE"
        ),
    }


def build_episode_decision_ledger(
    state_path: str | Path,
    *,
    trades: Sequence[Mapping[str, Any]] = (),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build one causal row per started policy episode.

    Policy-time decisions and any later offline outcome are deliberately
    separate.  This function does not infer a missed-trade label from future
    prices; it only joins exact native trade/order identities which actually
    occurred during the replay.
    """

    path = Path(state_path)
    if not path.is_file():
        return [], {
            "started_episodes": 0,
            "terminal_episodes": 0,
            "ongoing_episodes": 0,
            "selected_episodes": 0,
            "no_trade_episodes": 0,
            "terminal_reason_counts": {},
            "chart_coverage_counts": {},
            "policy_fingerprints": [],
            "offline_future_outcome_status": "NOT_EVALUATED_NOT_POLICY_EVIDENCE",
        }
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(runtime_events)")
        }
        event_key_sql = "event_key" if "event_key" in columns else "NULL AS event_key"
        event_rows = connection.execute(
            f"SELECT sequence, time_ns, event_type, payload_json, {event_key_sql} "
            "FROM runtime_events ORDER BY sequence",
        ).fetchall()
        starts: dict[str, dict[str, Any]] = {}
        terminals: dict[str, dict[str, Any]] = {}
        submitted_client_by_plan: dict[str, str | None] = {}
        accepted_clients: set[str] = set()
        filled_clients: set[str] = set()
        canceled_by_plan: dict[str, str] = {}
        for raw in event_rows:
            event_type = str(raw["event_type"])
            payload = json.loads(raw["payload_json"])
            if event_type in {"POLICY_EPISODE_STARTED", "POLICY_EPISODE_TERMINAL"}:
                episode_id = payload.get("episode_id")
                if not isinstance(episode_id, str) or not episode_id:
                    raise RuntimeError(f"malformed {event_type} episode identity")
                expected_key = f"{event_type}:{episode_id}"
                if raw["event_key"] != expected_key:
                    raise RuntimeError(
                        f"semantic event key mismatch for episode {episode_id}",
                    )
                target = starts if event_type == "POLICY_EPISODE_STARTED" else terminals
                if episode_id in target:
                    raise RuntimeError(f"duplicate {event_type} for episode {episode_id}")
                target[episode_id] = payload
                continue
            if event_type in {
                "PARENT_SUBMISSION_INTENT",
                "PARENT_ORDER_SUBMITTED",
                "PARENT_LIMIT_SUBMITTED",
            }:
                plan = payload.get("plan")
                plan_id = plan.get("plan_id") if isinstance(plan, Mapping) else None
                client_order_id = payload.get("client_order_id")
                if isinstance(plan_id, str) and isinstance(client_order_id, str):
                    prior = submitted_client_by_plan.get(plan_id)
                    if plan_id in submitted_client_by_plan and prior != client_order_id:
                        submitted_client_by_plan[plan_id] = None
                    elif plan_id not in submitted_client_by_plan:
                        submitted_client_by_plan[plan_id] = client_order_id
                continue
            if event_type == "ORDER_ACCEPTED":
                client_order_id = payload.get("client_order_id")
                if isinstance(client_order_id, str):
                    accepted_clients.add(client_order_id)
                continue
            if event_type == "ORDER_FILLED":
                client_order_id = payload.get("client_order_id")
                if isinstance(client_order_id, str):
                    filled_clients.add(client_order_id)
                continue
            if event_type == "PENDING_PLAN_CANCELED":
                plan_id = payload.get("plan_id")
                reason = payload.get("reason")
                if isinstance(plan_id, str) and isinstance(reason, str):
                    prior = canceled_by_plan.get(plan_id)
                    if prior is not None and prior != reason:
                        raise RuntimeError(f"conflicting cancellation for plan {plan_id}")
                    canceled_by_plan[plan_id] = reason

        orphan_terminals = sorted(set(terminals) - set(starts))
        if orphan_terminals:
            raise RuntimeError(
                f"terminal policy decisions without starts: {orphan_terminals[:3]}",
            )
        replay_end = connection.execute(
            "SELECT MAX(close_time_ns) FROM bars WHERE interval_minutes=1",
        ).fetchone()[0]

        trade_by_episode: dict[str, list[Mapping[str, Any]]] = {}
        for trade in trades:
            episode_id = trade.get("episode_id")
            if isinstance(episode_id, str):
                trade_by_episode.setdefault(episode_id, []).append(trade)

        output: list[dict[str, Any]] = []
        for episode_id, start in starts.items():
            terminal = terminals.get(episode_id)
            is_terminal = terminal is not None
            source = terminal if terminal is not None else start
            plan_id = source.get("plan_id") if is_terminal else None
            matching = list(trade_by_episode.get(episode_id, ()))
            if isinstance(plan_id, str):
                exact = [item for item in matching if item.get("plan_id") == plan_id]
                if exact:
                    matching = exact
            if len(matching) > 1:
                trade_join_status = "MULTIPLE_NATIVE_TRADE_ROWS"
                joined_trade = None
            elif matching:
                trade_join_status = "EXACT_EPISODE_PLAN" if plan_id else "EXACT_EPISODE"
                joined_trade = matching[0]
            else:
                trade_join_status = "NO_NATIVE_CLOSED_TRADE"
                joined_trade = None
            if not is_terminal:
                execution_disposition = "ONGOING_POLICY_WATCH"
            elif source.get("outcome") == "NO_TRADE":
                execution_disposition = "NOT_SELECTED"
            elif joined_trade is not None:
                execution_disposition = "FILLED_CLOSED"
            elif isinstance(plan_id, str) and plan_id in canceled_by_plan:
                execution_disposition = f"UNFILLED_CANCELED:{canceled_by_plan[plan_id]}"
            else:
                client_order_id = (
                    submitted_client_by_plan.get(plan_id)
                    if isinstance(plan_id, str)
                    else None
                )
                if client_order_id in filled_clients:
                    execution_disposition = "FILLED_OPEN_OR_REPORT_MISSING"
                elif client_order_id in accepted_clients:
                    execution_disposition = "ACCEPTED_UNRESOLVED"
                elif client_order_id is not None:
                    execution_disposition = "SUBMITTED_UNRESOLVED"
                else:
                    execution_disposition = "SELECTED_NO_EXECUTION_EVENT"

            chart_start = int(start["chart_start_time_ns"])
            chart_end_value = (
                source.get("chart_end_time_ns") if is_terminal else replay_end
            )
            chart_end = int(chart_end_value) if chart_end_value is not None else chart_start
            coverage = _chart_coverage(
                connection,
                symbol=str(start["symbol"]),
                start_time_ns=chart_start,
                end_time_ns=chart_end,
            )
            plan = source.get("plan") if isinstance(source.get("plan"), Mapping) else None
            overlays = {
                name: source.get(name)
                for name in ("entry", "stop", "target")
                if source.get(name) is not None
            }
            overlays.update(
                {
                    "source_lower": start.get("interaction_source_lower"),
                    "source_upper": start.get("interaction_source_upper"),
                },
            )
            row = {
                "decision_id": source.get("decision_id"),
                "episode_id": episode_id,
                "episode_status": "TERMINAL" if is_terminal else "ONGOING_AT_REPLAY_END",
                "outcome": source.get("outcome") if is_terminal else None,
                "terminal_stage": source.get("terminal_stage") if is_terminal else None,
                "terminal_reason": source.get("terminal_reason") if is_terminal else None,
                "symbol": start.get("symbol"),
                "family": source.get("family", start.get("family")),
                "side": source.get("side", start.get("side")),
                "started_time_ns": start.get("started_time_ns"),
                "interaction_time_ns": start.get("interaction_time_ns"),
                "terminal_time_ns": source.get("terminal_time_ns") if is_terminal else None,
                "source_boundary_id": start.get("source_boundary_id"),
                "source_kind": start.get("source_kind"),
                "source_side": start.get("source_side"),
                "source_timeframe_minutes": start.get("source_timeframe_minutes"),
                "source_observed_time_ns": start.get("source_observed_time_ns"),
                "interaction_source_lower": start.get("interaction_source_lower"),
                "interaction_source_upper": start.get("interaction_source_upper"),
                "journey_terminal_state": source.get("journey_terminal_state"),
                "journey_completed_states": source.get("journey_completed_states"),
                "plan_id": plan_id,
                "destination_boundary_id": source.get("destination_boundary_id"),
                "entry": source.get("entry"),
                "stop": source.get("stop"),
                "target": source.get("target"),
                "gross_rr": source.get("gross_rr"),
                "execution_disposition": execution_disposition,
                "trade_join_status": trade_join_status,
                "trade_id": None if joined_trade is None else joined_trade.get("trade_id"),
                "trade_outcome": None if joined_trade is None else joined_trade.get("outcome"),
                "trade_entry_time_ns": (
                    None if joined_trade is None else joined_trade.get("entry_time_ns")
                ),
                "trade_exit_time_ns": (
                    None if joined_trade is None else joined_trade.get("exit_time_ns")
                ),
                "offline_future_outcome": "NOT_EVALUATED",
                "offline_future_outcome_basis": "SEPARATE_FROM_POLICY_EVIDENCE",
                "window_id": f"episode:{episode_id}",
                "case_kind": (
                    "TRADE"
                    if joined_trade is not None
                    else "NO_TRADE"
                    if is_terminal
                    else "ONGOING"
                ),
                "case_id": episode_id,
                "anchor_time_ns": start.get("interaction_time_ns"),
                "window_start_time_ns": chart_start,
                "window_end_time_ns": chart_end,
                "overlays_json": _canonical_json(overlays),
                "plan_json": None if plan is None else _canonical_json(plan),
                "evidence_json": _canonical_json(source.get("evidence", {})),
                **coverage,
            }
            output.append(row)

    output.sort(
        key=lambda item: (
            int(item.get("interaction_time_ns") or 0),
            str(item.get("symbol") or ""),
            str(item["episode_id"]),
        ),
    )
    reason_counts: dict[str, int] = {}
    coverage_counts: dict[str, int] = {}
    for item in output:
        reason = item.get("terminal_reason")
        if isinstance(reason, str):
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        coverage = str(item["chart_coverage_status"])
        coverage_counts[coverage] = coverage_counts.get(coverage, 0) + 1
    terminal_count = sum(item["episode_status"] == "TERMINAL" for item in output)
    metrics = {
        "started_episodes": len(output),
        "terminal_episodes": terminal_count,
        "ongoing_episodes": len(output) - terminal_count,
        "selected_episodes": sum(item.get("outcome") == "SELECTED" for item in output),
        "no_trade_episodes": sum(item.get("outcome") == "NO_TRADE" for item in output),
        "terminal_reason_counts": dict(sorted(reason_counts.items())),
        "chart_coverage_counts": dict(sorted(coverage_counts.items())),
        "policy_fingerprints": sorted(
            {
                str(start.get("policy_fingerprint"))
                for start in starts.values()
                if start.get("policy_fingerprint") is not None
            },
        ),
        "offline_future_outcome_status": "NOT_EVALUATED_NOT_POLICY_EVIDENCE",
    }
    if metrics["started_episodes"] != metrics["terminal_episodes"] + metrics["ongoing_episodes"]:
        raise RuntimeError("episode decision partition invariant failed")
    return output, metrics


def _slippage_by_order(fills: Any) -> dict[str, float]:
    output: dict[str, float] = {}
    for row in _records(fills):
        order_id = row.get("client_order_id", row.get("index"))
        slippage = _number(row.get("slippage"))
        quantity = _number(row.get("filled_qty"))
        if quantity is None:
            quantity = _number(row.get("quantity"))
        if order_id is None or slippage is None or quantity is None:
            continue
        key = str(order_id)
        output[key] = output.get(key, 0.0) + abs(slippage * quantity)
    return output


def build_closed_trade_ledger(
    positions: Any,
    fills: Any,
    *,
    state_path: str | Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Normalize closed native positions and exact plan/order joins."""

    parents, order_roles, event_count = _load_parent_evidence(state_path)
    slippage = _slippage_by_order(fills)
    ledger: list[dict[str, Any]] = []
    open_position_rows = 0
    closed_report_rows = 0
    duplicate_closed_rows = 0
    lifecycle_signatures: dict[tuple[Any, ...], tuple[Any, ...]] = {}
    for row in _records(positions):
        exit_time = _time_ns(row.get("ts_closed"))
        if exit_time is None:
            open_position_rows += 1
            continue
        closed_report_rows += 1
        position_id = _position_id(row)
        opening_order_id = None if _missing(row.get("opening_order_id")) else str(row["opening_order_id"])
        closing_order_id = None if _missing(row.get("closing_order_id")) else str(row["closing_order_id"])
        entry_time = _time_ns(row.get("ts_opened"))
        lifecycle_identity = (position_id, opening_order_id, entry_time, exit_time)
        report_signature = (
            lifecycle_identity,
            closing_order_id,
            str(row.get("instrument_id") or ""),
            str(row.get("entry") or ""),
            str(row.get("side") or ""),
            _number(row.get("peak_qty")),
            _number(row.get("quantity")),
            _number(row.get("avg_px_open")),
            _number(row.get("avg_px_close")),
            _money_total(row.get("commissions")),
            _number(row.get("realized_pnl")),
            _number(row.get("duration_ns")),
        )
        prior_signature = lifecycle_signatures.get(lifecycle_identity)
        if prior_signature is not None:
            if prior_signature != report_signature:
                raise RuntimeError(
                    "conflicting closed NETTING lifecycle snapshots for "
                    f"position={position_id} opening_order={opening_order_id} ",
                )
            duplicate_closed_rows += 1
            continue
        lifecycle_signatures[lifecycle_identity] = report_signature
        parent = parents.get(opening_order_id or "")
        plan = parent.get("plan", {}) if isinstance(parent, Mapping) else {}
        sizing = parent.get("sizing", {}) if isinstance(parent, Mapping) else {}
        reported_side = str(row.get("side") or "").upper()
        entry_direction = str(row.get("entry") or "").upper()
        if reported_side in {"LONG", "SHORT"}:
            side = reported_side
        elif entry_direction in {"BUY", "LONG"}:
            side = "LONG"
        elif entry_direction in {"SELL", "SHORT"}:
            side = "SHORT"
        else:
            planned_side = str(plan.get("side") or "").upper() if isinstance(plan, Mapping) else ""
            side = planned_side if planned_side in {"LONG", "SHORT"} else None
        quantity = _number(row.get("peak_qty"))
        if quantity is None:
            quantity = _number(row.get("quantity"))
        entry_price = _number(row.get("avg_px_open"))
        exit_price = _number(row.get("avg_px_close"))
        gross_pnl = None
        if quantity is not None and entry_price is not None and exit_price is not None:
            if side == "LONG":
                gross_pnl = (exit_price - entry_price) * abs(quantity)
            elif side == "SHORT":
                gross_pnl = (entry_price - exit_price) * abs(quantity)
        fees = _money_total(row.get("commissions"))
        net_pnl = _number(row.get("realized_pnl"))
        funding_cost = None
        if gross_pnl is not None and fees is not None and net_pnl is not None:
            funding_cost = gross_pnl - fees - net_pnl
            if abs(funding_cost) < 1e-9:
                funding_cost = 0.0
        risk_cash = _number(sizing.get("planned_stop_loss")) if isinstance(sizing, Mapping) else None
        if risk_cash is not None:
            risk_cash = abs(risk_cash)
            if risk_cash == 0.0:
                risk_cash = None
        gross_r = gross_pnl / risk_cash if gross_pnl is not None and risk_cash else None
        net_r = net_pnl / risk_cash if net_pnl is not None and risk_cash else None
        ledger.append(
            {
                "trade_id": f"{position_id}@{opening_order_id or entry_time}:{exit_time}",
                "position_id": position_id,
                "opening_order_id": opening_order_id,
                "closing_order_id": closing_order_id,
                "plan_join_status": "EXACT_OPENING_ORDER_ID" if parent is not None else "UNKNOWN_NO_PARENT_EVENT",
                "plan_id": plan.get("plan_id") if isinstance(plan, Mapping) else None,
                "episode_id": plan.get("episode_id") if isinstance(plan, Mapping) else None,
                "family": plan.get("family") if isinstance(plan, Mapping) else None,
                "symbol": _symbol(row.get("instrument_id")),
                "side": side,
                "entry_time_ns": entry_time,
                "exit_time_ns": exit_time,
                "duration_ns": _number(row.get("duration_ns")),
                "quantity": quantity,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "planned_entry": _number(plan.get("entry")) if isinstance(plan, Mapping) else None,
                "stop_price": _number(plan.get("stop")) if isinstance(plan, Mapping) else None,
                "target_price": _number(plan.get("target")) if isinstance(plan, Mapping) else None,
                "planned_gross_rr": _number(plan.get("gross_rr")) if isinstance(plan, Mapping) else None,
                "risk_cash": risk_cash,
                "planned_risk_fraction": (
                    _number(sizing.get("planned_risk_fraction")) if isinstance(sizing, Mapping) else None
                ),
                "gross_pnl": gross_pnl,
                "fees": fees,
                "funding_cost": funding_cost,
                "reported_slippage_cost": (
                    sum(
                        slippage[order_id]
                        for order_id in (opening_order_id, closing_order_id)
                        if order_id is not None and order_id in slippage
                    )
                    if any(
                        order_id is not None and order_id in slippage
                        for order_id in (opening_order_id, closing_order_id)
                    ) else None
                ),
                "slippage_join_status": (
                    "EXACT_REPORTED_OPENING_AND_FINAL_CLOSING_ORDER_IDS_ONLY"
                    if opening_order_id in slippage and closing_order_id in slippage
                    else "PARTIAL_EXACT_ORDER_IDS"
                    if opening_order_id in slippage or closing_order_id in slippage
                    else "UNKNOWN_NO_MATCHED_ORDER_IDS"
                ),
                "net_pnl": net_pnl,
                "gross_r": gross_r,
                "net_r": net_r,
                "cost_after_r": net_r,
                "outcome": (
                    "WIN" if net_pnl is not None and net_pnl > 0
                    else "LOSS" if net_pnl is not None and net_pnl < 0
                    else "FLAT" if net_pnl == 0
                    else "UNKNOWN"
                ),
                "exit_reason": order_roles.get(
                    closing_order_id or "",
                    "UNKNOWN_NOT_EXPOSED_BY_NATIVE_POSITION_REPORT",
                ),
                "cost_bridge_basis": (
                    "native_actual_fill_gross_minus_commissions_and_funding_adjustment"
                    if funding_cost is not None else "UNKNOWN_INCOMPLETE_NATIVE_FIELDS"
                ),
            },
        )
    ledger.sort(key=lambda item: (item["exit_time_ns"], item.get("trade_id") or ""))
    return ledger, {
        "runtime_event_count": event_count,
        "parent_evidence_count": len(parents),
        "closed_position_report_rows": closed_report_rows,
        "closed_position_rows": len(ledger),
        "deduplicated_closed_report_rows": duplicate_closed_rows,
        "open_position_rows_excluded": open_position_rows,
        "exact_plan_joins": sum(item["plan_join_status"] == "EXACT_OPENING_ORDER_ID" for item in ledger),
        "unknown_plan_joins": sum(item["plan_join_status"] != "EXACT_OPENING_ORDER_ID" for item in ledger),
    }


def _account_observations(account: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for sequence, row in enumerate(_records(account)):
        currency = str(row.get("currency") or "")
        if currency and currency != "USDT":
            continue
        time_value = row.get("index", row.get("ts_event", row.get("timestamp")))
        time_ns = _time_ns(time_value)
        total = _number(row.get("total"))
        if time_ns is not None and total is not None:
            output.append({"time_ns": time_ns, "equity": total, "sequence": sequence})
    return sorted(output, key=lambda item: (item["time_ns"], item["sequence"]))


def _native_fill_events(fills: Any, state_path: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    order_sides: dict[str, str] = {}
    report_quantities: dict[str, Decimal] = {}
    for row in _records(fills):
        order_id = row.get("client_order_id", row.get("index"))
        side = str(row.get("side") or "").upper()
        quantity = _decimal_number(row.get("filled_qty"))
        if order_id is None or quantity is None or quantity <= 0:
            continue
        key = str(order_id)
        if side in {"BUY", "SELL"}:
            prior_side = order_sides.get(key)
            if prior_side is not None and prior_side != side:
                raise RuntimeError(f"conflicting native fill side for {key}")
            order_sides[key] = side
        report_quantities[key] = report_quantities.get(key, Decimal(0)) + abs(quantity)
    path = Path(state_path)
    if not path.is_file():
        return [], {
            "fill_events": 0,
            "unresolved_fill_events": 0,
            "native_fill_orders": len(report_quantities),
            "db_fill_orders": 0,
            "missing_db_fill_orders": sorted(report_quantities),
            "missing_native_fill_orders": [],
            "quantity_mismatch_orders": [],
            "fill_bijection_valid": not report_quantities,
        }
    with closing(sqlite3.connect(path)) as connection:
        rows = connection.execute(
            "SELECT time_ns, payload_json FROM runtime_events "
            "WHERE event_type='ORDER_FILLED' ORDER BY sequence",
        ).fetchall()
    output: list[dict[str, Any]] = []
    unresolved = 0
    raw_event_quantities: dict[str, Decimal] = {}
    for time_ns, payload_json in rows:
        payload = json.loads(payload_json)
        order_id = str(payload.get("client_order_id") or "")
        side = order_sides.get(order_id)
        quantity = _decimal_number(payload.get("last_qty"))
        price = _number(payload.get("last_px"))
        symbol = _symbol(payload.get("instrument_id"))
        if order_id and quantity is not None:
            raw_event_quantities[order_id] = (
                raw_event_quantities.get(order_id, Decimal(0)) + abs(quantity)
            )
        if side is None or quantity is None or price is None or symbol is None:
            unresolved += 1
            continue
        output.append(
            {
                "client_order_id": order_id,
                "time_ns": int(time_ns),
                "symbol": symbol,
                "side": side,
                "quantity": float(abs(quantity)),
                "price": price,
            },
        )
    missing_db = sorted(set(report_quantities) - set(raw_event_quantities))
    missing_native = sorted(set(raw_event_quantities) - set(report_quantities))
    mismatches = sorted(
        order_id
        for order_id in set(report_quantities) & set(raw_event_quantities)
        if report_quantities[order_id] != raw_event_quantities[order_id]
    )
    valid = not (unresolved or missing_db or missing_native or mismatches)
    return output, {
        "fill_events": len(rows),
        "unresolved_fill_events": unresolved,
        "native_fill_orders": len(report_quantities),
        "db_fill_orders": len(raw_event_quantities),
        "missing_db_fill_orders": missing_db,
        "missing_native_fill_orders": missing_native,
        "quantity_mismatch_orders": mismatches,
        "fill_bijection_valid": valid,
    }


def _apply_fill(position: dict[str, float], *, side: str, quantity: float, price: float) -> None:
    signed_fill = quantity if side == "BUY" else -quantity
    current = position.get("quantity", 0.0)
    average = position.get("average_price", 0.0)
    if current == 0.0 or current * signed_fill > 0.0:
        new_quantity = current + signed_fill
        position["average_price"] = (
            (abs(current) * average + abs(signed_fill) * price) / abs(new_quantity)
        )
        position["quantity"] = new_quantity
        return
    if abs(signed_fill) < abs(current):
        position["quantity"] = current + signed_fill
        return
    if abs(signed_fill) == abs(current):
        position["quantity"] = 0.0
        position["average_price"] = 0.0
        return
    position["quantity"] = current + signed_fill
    position["average_price"] = price


def _minute_mtm_equity(
    minutes: Iterable[Any],
    *,
    observations: Sequence[Mapping[str, Any]],
    fill_events: Sequence[Mapping[str, Any]],
    start: date,
    end: date,
    initial_nav: float,
    fill_diagnostics: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if fill_diagnostics.get("fill_bijection_valid") is not True:
        raise ValueError("cannot reconstruct minute MTM without exact native/SQLite fill bijection")
    balance_offset = 0
    fill_offset = 0
    balance = initial_nav
    positions: dict[str, dict[str, float]] = {}
    peak = initial_nav
    maximum_drawdown = 0.0
    observations_count = 1
    daily_last: dict[str, tuple[int, float]] = {}
    for minute in minutes:
        time_ns = int(minute.ts_event)
        while balance_offset < len(observations) and observations[balance_offset]["time_ns"] <= time_ns:
            balance = float(observations[balance_offset]["equity"])
            balance_offset += 1
        while fill_offset < len(fill_events) and fill_events[fill_offset]["time_ns"] <= time_ns:
            fill = fill_events[fill_offset]
            position = positions.setdefault(fill["symbol"], {"quantity": 0.0, "average_price": 0.0})
            _apply_fill(position, side=fill["side"], quantity=fill["quantity"], price=fill["price"])
            fill_offset += 1
        unrealized = 0.0
        for symbol, position in positions.items():
            quantity = position["quantity"]
            if abs(quantity) < 1e-15:
                continue
            native_bar = minute.bars.get(symbol)
            if native_bar is None:
                raise ValueError(f"minute MTM missing native mark for open {symbol}")
            unrealized += quantity * (float(native_bar.close) - position["average_price"])
        equity = balance + unrealized
        peak = max(peak, equity)
        if peak > 0:
            maximum_drawdown = max(maximum_drawdown, 1.0 - equity / peak)
        observations_count += 1
        candle_date = datetime.fromtimestamp(
            (time_ns - 1) // 1_000_000_000,
            tz=UTC,
        ).date().isoformat()
        daily_last[candle_date] = (time_ns, equity)

    daily: list[dict[str, Any]] = []
    cursor = start
    prior = initial_nav
    daily_peak = initial_nav
    while cursor < end:
        observed = daily_last.get(cursor.isoformat())
        equity = observed[1] if observed is not None else (daily[-1]["equity"] if daily else initial_nav)
        time_ns = observed[0] if observed is not None else int(
            datetime.combine(cursor + timedelta(days=1), datetime.min.time(), tzinfo=UTC).timestamp() * 1e9
        )
        daily_peak = max(daily_peak, equity)
        daily.append(
            {
                "date": cursor.isoformat(),
                "time_ns": time_ns,
                "equity": equity,
                "daily_return": equity / prior - 1.0 if prior != 0 else None,
                "drawdown": 1.0 - equity / daily_peak if daily_peak > 0 else None,
                "equity_basis": "NATIVE_ACCOUNT_TOTAL_PLUS_FILL_RECONSTRUCTED_1M_MTM",
                "includes_unrealized_pnl": True,
            },
        )
        prior = equity
        cursor += timedelta(days=1)
    return daily, {
        "maximum_continuous_drawdown": maximum_drawdown,
        "drawdown_observations": observations_count,
        "drawdown_basis": "CONTINUOUS_COMPLETED_1M_NATIVE_ACCOUNT_PLUS_FILL_MTM",
        "continuous_1m_mtm_drawdown": maximum_drawdown,
        "continuous_intrabar_mtm_drawdown": None,
        "continuous_intrabar_mtm_drawdown_status": "UNKNOWN_1M_OHLC_HAS_NO_INTRABAR_EQUITY_PATH",
        "daily_equity_basis": "NATIVE_ACCOUNT_TOTAL_PLUS_FILL_RECONSTRUCTED_MTM_AT_UTC_DAY_END",
        "daily_equity_includes_unrealized_pnl": True,
        "daily_equity_fill_diagnostics": dict(fill_diagnostics),
    }


def build_daily_equity(
    account: Any,
    *,
    start: date,
    end: date,
    initial_nav: float,
    fills: Any = None,
    state_path: str | Path | None = None,
    daily_marks: Mapping[str, Mapping[str, float]] | None = None,
    equity_minutes: Iterable[Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build daily native account-total observations for ``[start, end)``."""

    observations = _account_observations(account)
    fill_events, fill_diagnostics = _native_fill_events(fills, state_path) if state_path else (
        [], {"fill_events": 0, "unresolved_fill_events": 0}
    )
    if equity_minutes is not None:
        return _minute_mtm_equity(
            equity_minutes,
            observations=observations,
            fill_events=fill_events,
            start=start,
            end=end,
            initial_nav=initial_nav,
            fill_diagnostics=fill_diagnostics,
        )
    can_reconstruct_mtm = (
        daily_marks is not None and fill_diagnostics.get("fill_bijection_valid") is True
    )

    daily: list[dict[str, Any]] = []
    cursor = start
    offset = 0
    last = initial_nav
    prior_daily = initial_nav
    daily_peak = initial_nav
    fill_offset = 0
    positions: dict[str, dict[str, float]] = {}
    known_daily_equity: list[float] = []
    while cursor < end:
        boundary = cursor + timedelta(days=1)
        boundary_ns = int(datetime(boundary.year, boundary.month, boundary.day, tzinfo=UTC).timestamp() * 1e9)
        while offset < len(observations) and observations[offset]["time_ns"] <= boundary_ns:
            last = observations[offset]["equity"]
            offset += 1
        while fill_offset < len(fill_events) and fill_events[fill_offset]["time_ns"] <= boundary_ns:
            fill = fill_events[fill_offset]
            position = positions.setdefault(fill["symbol"], {"quantity": 0.0, "average_price": 0.0})
            _apply_fill(position, side=fill["side"], quantity=fill["quantity"], price=fill["price"])
            fill_offset += 1
        equity: float | None = last
        basis = "NATIVE_ACCOUNT_TOTAL"
        includes_unrealized = False
        if can_reconstruct_mtm:
            marks = daily_marks.get(cursor.isoformat(), {})
            unrealized = 0.0
            complete = True
            for symbol, position in positions.items():
                quantity = position["quantity"]
                if abs(quantity) < 1e-15:
                    continue
                mark = _number(marks.get(symbol))
                if mark is None:
                    complete = False
                    break
                unrealized += quantity * (mark - position["average_price"])
            if complete:
                equity = last + unrealized
                basis = "NATIVE_ACCOUNT_TOTAL_PLUS_FILL_RECONSTRUCTED_MTM"
                includes_unrealized = True
            else:
                equity = None
                basis = "UNKNOWN_MISSING_OPEN_POSITION_MARK"
        if equity is not None:
            daily_peak = max(daily_peak, equity)
            known_daily_equity.append(equity)
        daily.append(
            {
                "date": cursor.isoformat(),
                "time_ns": boundary_ns,
                "equity": equity,
                "daily_return": (
                    equity / prior_daily - 1.0
                    if equity is not None and prior_daily != 0 else None
                ),
                "drawdown": (
                    1.0 - equity / daily_peak if equity is not None and daily_peak > 0 else None
                ),
                "equity_basis": basis,
                "includes_unrealized_pnl": includes_unrealized,
            },
        )
        if equity is not None:
            prior_daily = equity
        cursor = boundary
    peak = initial_nav
    maximum_drawdown = 0.0
    for value in known_daily_equity:
        peak = max(peak, value)
        if peak > 0:
            maximum_drawdown = max(maximum_drawdown, 1.0 - value / peak)
    return daily, {
        "maximum_continuous_drawdown": maximum_drawdown,
        "drawdown_observations": len(known_daily_equity) + 1,
        "drawdown_basis": (
            "DAILY_NATIVE_ACCOUNT_PLUS_FILL_RECONSTRUCTED_MTM"
            if can_reconstruct_mtm else "DAILY_NATIVE_ACCOUNT_TOTAL"
        ),
        "continuous_intraday_mtm_drawdown": None,
        "continuous_intraday_mtm_drawdown_status": "UNKNOWN_NO_INTRADAY_EQUITY_SERIES",
        "continuous_mtm_drawdown": None,
        "continuous_mtm_drawdown_status": "UNKNOWN_NO_INTRADAY_EQUITY_SERIES",
        "daily_equity_basis": (
            "NATIVE_ACCOUNT_TOTAL_PLUS_FILL_RECONSTRUCTED_MTM_AT_UTC_DAY_END"
            if can_reconstruct_mtm else "NATIVE_ACCOUNT_TOTAL_FORWARD_FILLED_AT_UTC_DAY_END"
        ),
        "daily_equity_includes_unrealized_pnl": can_reconstruct_mtm,
        "daily_equity_fill_diagnostics": fill_diagnostics,
    }


def _profit_factor(values: Sequence[float]) -> float | None:
    profit = sum(max(value, 0.0) for value in values)
    loss = -sum(min(value, 0.0) for value in values)
    return profit / loss if loss > 0 else None


def _mean(values: Iterable[float | None]) -> float | None:
    known = [value for value in values if value is not None and math.isfinite(value)]
    return sum(known) / len(known) if known else None


def _overlap_pairs(ledger: Sequence[Mapping[str, Any]]) -> int | None:
    intervals: list[tuple[int, int]] = []
    for item in ledger:
        start, end = item.get("entry_time_ns"), item.get("exit_time_ns")
        if start is None or end is None:
            return None
        intervals.append((int(start), int(end)))
    intervals.sort()
    pairs = 0
    for index, (start, _) in enumerate(intervals):
        pairs += sum(prior_end > start for _, prior_end in intervals[:index])
    return pairs


def build_replay_evidence(
    *,
    positions: Any,
    fills: Any,
    account: Any,
    state_path: str | Path,
    start: date,
    end: date,
    initial_nav: float,
    final_nav: float,
    daily_marks: Mapping[str, Mapping[str, float]] | None = None,
    final_cash_balance: float | None = None,
    equity_minutes: Iterable[Any] | None = None,
) -> dict[str, Any]:
    ledger, join = build_closed_trade_ledger(positions, fills, state_path=state_path)
    episode_decisions, episode_decision_metrics = build_episode_decision_ledger(
        state_path,
        trades=ledger,
    )
    daily, equity_metrics = build_daily_equity(
        account,
        start=start,
        end=end,
        initial_nav=initial_nav,
        fills=fills,
        state_path=state_path,
        daily_marks=daily_marks,
        equity_minutes=equity_minutes,
    )
    net = [float(item["net_pnl"]) for item in ledger if item.get("net_pnl") is not None]
    overlap = _overlap_pairs(ledger)
    episodes = [item["episode_id"] for item in ledger if item.get("episode_id") is not None]
    per_symbol: dict[str, dict[str, Any]] = {}
    for symbol in SYMBOLS:
        rows = [item for item in ledger if item.get("symbol") == symbol]
        pnls = [float(item["net_pnl"]) for item in rows if item.get("net_pnl") is not None]
        per_symbol[symbol] = {
            "closed_trades": len(rows),
            "known_net_pnl_trades": len(pnls),
            "win_rate": sum(value > 0 for value in pnls) / len(pnls) if pnls else None,
            "profit_factor": _profit_factor(pnls),
            "net_pnl": sum(pnls) if pnls else 0.0,
            "average_cost_after_r": _mean(item.get("cost_after_r") for item in rows),
            "average_planned_gross_rr": _mean(item.get("planned_gross_rr") for item in rows),
        }
    final_daily_equity = next(
        (item["equity"] for item in reversed(daily) if item.get("equity") is not None),
        None,
    )
    metrics = {
        "descriptive_only": True,
        "closed_trades": len(ledger),
        "known_net_pnl_trades": len(net),
        "win_rate": sum(value > 0 for value in net) / len(net) if net else None,
        "profit_factor": _profit_factor(net),
        "net_pnl": sum(net) if net else 0.0,
        "average_gross_r": _mean(item.get("gross_r") for item in ledger),
        "average_planned_gross_rr": _mean(item.get("planned_gross_rr") for item in ledger),
        "average_net_r": _mean(item.get("net_r") for item in ledger),
        "average_cost_after_r": _mean(item.get("cost_after_r") for item in ledger),
        "initial_nav": initial_nav,
        "final_cash_balance": final_cash_balance,
        "final_nav": final_nav,
        "final_unrealized_pnl": (
            final_nav - final_cash_balance if final_cash_balance is not None else None
        ),
        "final_daily_equity": final_daily_equity,
        "final_daily_equity_difference_from_native_nav": (
            final_daily_equity - final_nav if final_daily_equity is not None else None
        ),
        **equity_metrics,
        "symbol_aggregates": per_symbol,
        "overlap_invariant": {
            "overlapping_trade_pair_count": overlap,
            "status": (
                "OBSERVED_NO_OVERLAP" if overlap == 0
                else "OBSERVED_VIOLATION" if overlap is not None
                else "UNKNOWN_INCOMPLETE_INTERVALS"
            ),
        },
        "episode_identity": {
            "known_episode_ids": len(episodes),
            "unknown_episode_ids": len(ledger) - len(episodes),
            "duplicate_known_episode_id_count": len(episodes) - len(set(episodes)),
        },
        "plan_evidence_join": join,
        "episode_decisions": episode_decision_metrics,
    }
    return {
        "schema_version": 1,
        "metrics": metrics,
        "trades": ledger,
        "daily_equity": daily,
        "episode_decisions": episode_decisions,
    }


TRADE_FIELDS = (
    "trade_id", "position_id", "opening_order_id", "closing_order_id",
    "plan_join_status", "plan_id",
    "episode_id", "family", "symbol", "side", "entry_time_ns", "exit_time_ns",
    "duration_ns", "quantity", "entry_price", "exit_price", "planned_entry",
    "stop_price", "target_price", "planned_gross_rr", "risk_cash",
    "planned_risk_fraction", "gross_pnl", "fees", "funding_cost",
    "reported_slippage_cost", "slippage_join_status", "net_pnl", "gross_r",
    "net_r", "cost_after_r", "outcome",
    "exit_reason", "cost_bridge_basis",
)
DAILY_FIELDS = (
    "date", "time_ns", "equity", "daily_return", "drawdown", "equity_basis",
    "includes_unrealized_pnl",
)
EPISODE_DECISION_FIELDS = (
    "decision_id", "episode_id", "episode_status", "outcome",
    "terminal_stage", "terminal_reason", "symbol", "family", "side",
    "started_time_ns", "interaction_time_ns", "terminal_time_ns",
    "source_boundary_id", "source_kind", "source_side",
    "source_timeframe_minutes", "source_observed_time_ns",
    "interaction_source_lower", "interaction_source_upper",
    "journey_terminal_state", "journey_completed_states", "plan_id",
    "destination_boundary_id", "entry", "stop", "target", "gross_rr",
    "execution_disposition", "trade_join_status", "trade_id", "trade_outcome",
    "trade_entry_time_ns", "trade_exit_time_ns", "offline_future_outcome",
    "offline_future_outcome_basis", "window_id", "case_kind", "case_id",
    "anchor_time_ns", "window_start_time_ns", "window_end_time_ns",
    "chart_expected_bar_count", "chart_observed_bar_count",
    "chart_first_open_time_ns", "chart_last_close_time_ns",
    "chart_coverage_status", "overlays_json", "plan_json", "evidence_json",
)


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_replay_evidence(destination: str | Path, evidence: Mapping[str, Any]) -> None:
    root = Path(destination)
    _write_csv(root / "trades.csv", list(evidence.get("trades", [])), TRADE_FIELDS)
    _write_csv(root / "daily_equity.csv", list(evidence.get("daily_equity", [])), DAILY_FIELDS)
    _write_csv(
        root / "episode_decisions.csv",
        list(evidence.get("episode_decisions", [])),
        EPISODE_DECISION_FIELDS,
    )
    (root / "replay_evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "build_closed_trade_ledger",
    "build_daily_equity",
    "build_episode_decision_ledger",
    "build_replay_evidence",
    "write_replay_evidence",
]
