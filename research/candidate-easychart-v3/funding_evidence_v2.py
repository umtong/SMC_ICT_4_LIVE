"""Funding ledger reconciliation against unique completed trade episodes.

Nautilus NETTING reuses the live position ID for later positions. Historical
position snapshots in reports receive unique suffixes, so joining financing by
``position_id`` alone silently assigns several trades' funding to the last live
position. This module joins every settlement to the one completed trade whose
instrument, side, quantity and open interval contain that exact settlement.
"""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import pandas as pd

from backtest_support import RISK_TOLERANCE, _jsonable


def _as_ns(values: pd.Series, name: str) -> pd.Series:
    parsed = pd.to_datetime(values, utc=True, errors="coerce")
    if parsed.isna().any():
        bad = values[parsed.isna()].astype(str).tolist()
        raise RuntimeError(f"invalid {name} timestamps in trade audit: {bad}")
    return parsed.astype("int64")


def _match_settlement(
    audit: pd.DataFrame,
    opened_ns: pd.Series,
    closed_ns: pd.Series,
    record: dict[str, Any],
) -> int:
    instrument_id = str(record["instrument_id"])
    settlement_ns = int(record.get("settlement_time_ns", record["processed_time_ns"]))
    signed_qty = Decimal(str(record["signed_qty"]))
    expected_side = "LONG" if signed_qty > 0 else "SHORT"
    expected_qty = abs(float(signed_qty))
    quantity = pd.to_numeric(audit["quantity"], errors="coerce")
    candidate = (
        audit["instrument_id"].astype(str).eq(instrument_id)
        & audit["side"].astype(str).eq(expected_side)
        & opened_ns.le(settlement_ns)
        & closed_ns.gt(settlement_ns)
    )
    indices = audit.index[candidate].tolist()
    if len(indices) != 1:
        raise RuntimeError(
            "funding settlement did not map to exactly one open trade: "
            f"instrument={instrument_id}, settlement_ns={settlement_ns}, "
            f"side={expected_side}, candidates={indices}",
        )
    index = indices[0]
    actual_qty = float(quantity.loc[index])
    tolerance = max(1e-9, expected_qty * 1e-9)
    if abs(actual_qty - expected_qty) > tolerance:
        raise RuntimeError(
            "funding settlement quantity disagrees with matched trade: "
            f"expected={expected_qty}, actual={actual_qty}, record={record}",
        )
    return index


def write_funding_evidence(
    funding_module: Any,
    output: Path,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    audit_path = output / "trade_audit.csv"
    if not audit_path.exists():
        raise RuntimeError("trade_audit.csv unavailable for funding join")
    audit = pd.read_csv(audit_path)
    required = {
        "position_id",
        "plan_id",
        "opening_order_id",
        "instrument_id",
        "side",
        "quantity",
        "ts_opened",
        "ts_closed",
        "realized_pnl",
        "risk_budget",
    }
    missing = sorted(required - set(audit.columns))
    if missing:
        raise RuntimeError(f"trade audit lost funding join fields: {missing}")
    audit["position_id"] = audit["position_id"].astype(str)
    opened_ns = _as_ns(audit["ts_opened"], "open")
    closed_ns = _as_ns(audit["ts_closed"], "close")

    records = [dict(record) for record in funding_module.ledger]
    amounts_by_trade: dict[str, Decimal] = {}
    unmatched: list[dict[str, Any]] = []
    for record in records:
        try:
            index = _match_settlement(audit, opened_ns, closed_ns, record)
        except RuntimeError as exc:
            failed = dict(record)
            failed["match_error"] = str(exc)
            unmatched.append(failed)
            continue
        trade_position_id = str(audit.at[index, "position_id"])
        record["engine_position_id"] = str(record.pop("position_id"))
        record["trade_position_id"] = trade_position_id
        record["plan_id"] = audit.at[index, "plan_id"]
        record["opening_order_id"] = str(audit.at[index, "opening_order_id"])
        amount = Decimal(str(record["amount"]))
        amounts_by_trade[trade_position_id] = amounts_by_trade.get(
            trade_position_id,
            Decimal("0"),
        ) + amount

    columns = [
        "symbol",
        "instrument_id",
        "engine_position_id",
        "trade_position_id",
        "plan_id",
        "opening_order_id",
        "account_id",
        "strategy_id",
        "funding_time_ns",
        "settlement_time_ns",
        "processed_time_ns",
        "interval_minutes",
        "rate",
        "mark_price",
        "signed_qty",
        "notional",
        "currency",
        "amount",
    ]
    ledger = pd.DataFrame(records)
    if ledger.empty:
        ledger = pd.DataFrame(columns=columns)
    else:
        missing_columns = sorted(set(columns) - set(ledger.columns))
        if missing_columns and not unmatched:
            raise RuntimeError(f"funding ledger lost required columns: {missing_columns}")
        for column in missing_columns:
            ledger[column] = None
        ledger = ledger[columns]
    ledger.to_csv(output / "funding_ledger.csv", index=False)
    with (output / "funding_ledger.jsonl").open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(_jsonable(record), ensure_ascii=False, sort_keys=True) + "\n")

    funding_series = pd.Series(
        {key: float(value) for key, value in amounts_by_trade.items()},
        dtype=float,
    )
    audit["funding_pnl"] = audit["position_id"].map(funding_series).fillna(0.0)
    execution_pnl = pd.to_numeric(audit["realized_pnl"], errors="coerce")
    audit["realized_pnl_after_funding"] = execution_pnl + audit["funding_pnl"]
    risk_budget = pd.to_numeric(audit["risk_budget"], errors="coerce")
    audit["actual_net_r_after_funding"] = audit["realized_pnl_after_funding"] / risk_budget
    audit["actual_loss_budget_breach_after_funding"] = (
        (audit["realized_pnl_after_funding"] < 0.0)
        & (-audit["realized_pnl_after_funding"] > risk_budget * RISK_TOLERANCE)
    ).fillna(False)
    audit.to_csv(audit_path, index=False)

    funding_total = sum(
        (Decimal(str(record["amount"])) for record in records),
        Decimal("0"),
    )
    execution_total = float(execution_pnl.fillna(0.0).sum())
    expected_final_nav = (
        float(metrics["starting_nav"])
        + execution_total
        + float(funding_total)
    )
    return {
        "funding_boundaries_loaded": len(funding_module.boundaries),
        "funding_boundaries_processed": int(funding_module.processed_boundaries),
        "funding_position_settlements": int(funding_module.settled_positions),
        "funding_positions_charged_or_credited": len(amounts_by_trade),
        "funding_engine_position_ids": len(
            {str(record.get("engine_position_id", record.get("position_id"))) for record in records}
        ),
        "funding_total": float(funding_total),
        "execution_realized_pnl_total": execution_total,
        "realized_pnl_after_funding_total": execution_total + float(funding_total),
        "expected_final_nav_from_trade_and_funding_ledgers": expected_final_nav,
        "nav_reconciliation_error": float(metrics["final_nav"]) - expected_final_nav,
        # Keep the historical key consumed by the surrounding validator, but
        # surface full records rather than ambiguous recycled position IDs.
        "unmatched_funding_position_ids": unmatched,
        "actual_loss_budget_breaches_after_funding": int(
            audit["actual_loss_budget_breach_after_funding"].astype(bool).sum()
        ),
        "funding_trade_join": "instrument+side+quantity+open_interval",
    }


def correct_run_metadata(output: Path) -> None:
    """Replace legacy wording with the exact archive and join semantics used."""
    path = output / "run.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    funding = value.setdefault("funding", {})
    funding["source"] = (
        "Binance Vision monthly fundingRate plus the open of the one-minute "
        "markPriceKlines bar containing each exact archive calc_time"
    )
    funding["causal_mark_policy"] = (
        "open of the one-minute mark-price bar containing exact archive calc_time"
    )
    funding["settlement_time_policy"] = (
        "preserve exact calc_time for provenance; settle after strategy callbacks "
        "at the containing minute because execution data are one-minute bars"
    )
    funding["funding_trade_join"] = (
        "instrument + side + quantity + completed-trade open interval; "
        "never recycled NETTING position_id alone"
    )
    path.write_text(
        json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
