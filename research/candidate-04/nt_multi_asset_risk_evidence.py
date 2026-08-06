#!/usr/bin/env python3
"""Reconcile multi-asset realized losses with their causal entry NAV.

NautilusTrader owns every PnL value and the strategy owns every recorded entry
NAV.  This module joins those two evidence streams by instrument and chronological
open order; it never simulates or recalculates a fill.  Ambiguous evidence fails
closed so an integrated candidate cannot pass the 3% contract accidentally.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

import nt_backtest as single_base


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


@dataclass(frozen=True, slots=True)
class RiskEvidence:
    maximum_realized_loss_fraction: float
    matched_losses: int
    matched_positions: int
    matched_entries: int
    ordering: dict[str, str]
    pass_: bool
    errors: tuple[str, ...]


def normalized(value: Any) -> str:
    return "".join(character for character in str(value).lower() if character.isalnum())


def find_column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    normalized_columns = {normalized(column): str(column) for column in frame.columns}
    for candidate in candidates:
        key = normalized(candidate)
        if key in normalized_columns:
            return normalized_columns[key]
    for key, original in normalized_columns.items():
        if any(normalized(candidate) in key for candidate in candidates):
            return original
    return None


def position_instrument_column(frame: pd.DataFrame) -> str | None:
    return find_column(
        frame,
        (
            "instrument_id",
            "instrument",
            "symbol",
        ),
    )


def position_open_column(frame: pd.DataFrame) -> str | None:
    return find_column(
        frame,
        (
            "ts_opened",
            "ts_open",
            "open_time",
            "entry_time",
            "opened_at",
            "opening_time",
        ),
    )


def position_pnl_column(frame: pd.DataFrame) -> str | None:
    return find_column(
        frame,
        (
            "realized_pnl",
            "realized_return",
            "pnl",
        ),
    )


def sortable_series(series: pd.Series) -> pd.Series | None:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().all():
        return numeric
    timestamp = pd.to_datetime(series, errors="coerce", utc=True)
    if timestamp.notna().all():
        return timestamp.astype("int64")
    return None


def event_order(event: dict[str, Any], fallback: int) -> tuple[int, int]:
    for value in (
        event.get("event_timestamp"),
        event.get("timestamp"),
        (event.get("details") or {}).get("ts"),
        (event.get("details") or {}).get("ts_event"),
    ):
        if value is None:
            continue
        try:
            number = int(value)
            return number, fallback
        except (TypeError, ValueError):
            parsed = pd.to_datetime(value, errors="coerce", utc=True)
            if not pd.isna(parsed):
                return int(parsed.value), fallback
    return 2**63 - 1, fallback


def entry_events(path: Path) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"strategy event evidence is not a list: {path}")
    selected = [
        dict(item)
        for item in rows
        if isinstance(item, dict)
        and item.get("event_type") == "ENTRY_SUBMITTED"
    ]
    return [
        item
        for _, item in sorted(
            enumerate(selected),
            key=lambda pair: event_order(pair[1], pair[0]),
        )
    ]


def position_rows(
    positions: pd.DataFrame,
    symbol: str,
) -> tuple[list[dict[str, Any]], str]:
    instrument_column = position_instrument_column(positions)
    if instrument_column is None:
        raise ValueError("Nautilus positions report has no instrument column")
    mask = positions[instrument_column].astype(str).str.upper().str.contains(
        symbol,
        regex=False,
    )
    selected = positions.loc[mask].copy()
    open_column = position_open_column(selected)
    ordering = "report_order"
    if open_column is not None and not selected.empty:
        order = sortable_series(selected[open_column])
        if order is not None:
            selected = selected.assign(_open_order=order).sort_values(
                "_open_order",
                kind="stable",
            )
            selected = selected.drop(columns=["_open_order"])
            ordering = f"instrument_then_{open_column}"
    return selected.to_dict("records"), ordering


def as_number(value: Any) -> float | None:
    return single_base.as_number(value)


def reconcile_risk_evidence(
    positions: pd.DataFrame,
    events_by_symbol: dict[str, list[dict[str, Any]]],
    limit: float = 0.0301,
) -> RiskEvidence:
    errors: list[str] = []
    ordering: dict[str, str] = {}
    fractions: list[float] = []
    matched_positions = 0
    matched_entries = 0
    matched_losses = 0
    pnl_column = position_pnl_column(positions)
    if pnl_column is None and not positions.empty:
        errors.append("Nautilus positions report has no realized PnL column")

    for symbol in SYMBOLS:
        try:
            rows, method = position_rows(positions, symbol)
        except ValueError as exc:
            errors.append(str(exc))
            rows = []
            method = "unavailable"
        ordering[symbol] = method
        entries = [
            event
            for event in events_by_symbol.get(symbol, [])
            if event.get("event_type") == "ENTRY_SUBMITTED"
        ]
        entries = [
            event
            for _, event in sorted(
                enumerate(entries),
                key=lambda pair: event_order(pair[1], pair[0]),
            )
        ]
        if len(rows) != len(entries):
            errors.append(
                f"{symbol}: position-entry mismatch "
                f"positions={len(rows)} entries={len(entries)}"
            )
            continue
        matched_positions += len(rows)
        matched_entries += len(entries)
        if pnl_column is None:
            continue
        for row, entry in zip(rows, entries):
            pnl = as_number(row.get(pnl_column))
            equity = as_number((entry.get("details") or {}).get("equity"))
            if pnl is None or equity is None or equity <= 0.0:
                errors.append(f"{symbol}: nonnumeric PnL or entry equity")
                continue
            if pnl < 0.0:
                fraction = abs(pnl) / equity
                if not math.isfinite(fraction):
                    errors.append(f"{symbol}: nonfinite realized loss fraction")
                    continue
                fractions.append(fraction)
                matched_losses += 1

    if matched_positions != len(positions.index):
        errors.append(
            "not every Nautilus position was matched to one configured instrument"
        )
    maximum = max(fractions, default=0.0)
    passed = not errors and maximum <= limit
    return RiskEvidence(
        maximum_realized_loss_fraction=maximum,
        matched_losses=matched_losses,
        matched_positions=matched_positions,
        matched_entries=matched_entries,
        ordering=ordering,
        pass_=passed,
        errors=tuple(errors),
    )


def load_positions(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except EmptyDataError:
        return pd.DataFrame()


def load_events_by_symbol(output: Path) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for symbol in SYMBOLS:
        path = output / f"strategy_events-{symbol}.json"
        if not path.exists():
            result[symbol] = []
            continue
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise ValueError(f"invalid strategy event evidence: {path}")
        result[symbol] = [dict(item) for item in value if isinstance(item, dict)]
    return result


def reconcile_output(output: Path, limit: float = 0.0301) -> dict[str, Any]:
    metrics_path = output / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    positions = load_positions(output / "positions.csv")
    events = load_events_by_symbol(output)
    evidence = reconcile_risk_evidence(positions, events, limit)
    metrics["maximum_realized_loss_fraction"] = (
        evidence.maximum_realized_loss_fraction
    )
    metrics["multi_asset_risk_evidence"] = {
        "matched_losses": evidence.matched_losses,
        "matched_positions": evidence.matched_positions,
        "matched_entries": evidence.matched_entries,
        "ordering": evidence.ordering,
        "errors": list(evidence.errors),
        "limit": limit,
        "pass": evidence.pass_,
        "pnl_source": "NautilusTrader positions report",
        "entry_nav_source": "per-symbol strategy ENTRY_SUBMITTED evidence",
        "performance_recalculated": False,
    }
    checks = dict(metrics.get("gate_checks") or {})
    checks["realized_loss_within_3pct_nav"] = evidence.pass_
    metrics["gate_checks"] = checks
    metrics["risk_pass"] = evidence.pass_
    metrics["candidate_pass"] = bool(
        checks
        and all(bool(value) for value in checks.values())
        and metrics.get("global_entry_pass") is True
    )
    metrics_path.write_text(
        json.dumps(metrics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return metrics


__all__ = [
    "RiskEvidence",
    "entry_events",
    "reconcile_output",
    "reconcile_risk_evidence",
]
