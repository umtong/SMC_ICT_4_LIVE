"""Compact trade-by-trade diagnostics for Candidate 57 campaigns."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _money(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"none", "nan", "nat"}:
        return None
    try:
        result = float(text.split()[0])
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def _quantiles(values: Iterable[float]) -> dict[str, float | None]:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return {key: None for key in ("min", "q25", "median", "q75", "max")}

    def q(fraction: float) -> float:
        position = (len(ordered) - 1) * fraction
        lower, upper = math.floor(position), math.ceil(position)
        if lower == upper:
            return ordered[lower]
        weight = position - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

    return {
        "min": ordered[0],
        "q25": q(0.25),
        "median": q(0.50),
        "q75": q(0.75),
        "max": ordered[-1],
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [float(row["pnl_usdt"]) for row in rows if row["pnl_usdt"] is not None]
    rs = [float(row["actual_r"]) for row in rows if row["actual_r"] is not None]
    wins = [value for value in pnls if value > 0.0]
    losses = [-value for value in pnls if value < 0.0]
    positive_r = [value for value in rs if value > 0.0]
    negative_r = [-value for value in rs if value < 0.0]
    return {
        "trades": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": sum(abs(value) <= 1e-12 for value in pnls),
        "win_rate": len(wins) / len(pnls) if pnls else 0.0,
        "sum_pnl_usdt": sum(pnls),
        "mean_pnl_usdt": sum(pnls) / len(pnls) if pnls else None,
        "profit_factor_usdt": (
            sum(wins) / sum(losses)
            if losses and sum(losses) > 0.0
            else (None if wins else 0.0)
        ),
        "sum_r": sum(rs),
        "mean_r": sum(rs) / len(rs) if rs else None,
        "profit_factor_r": (
            sum(positive_r) / sum(negative_r)
            if negative_r and sum(negative_r) > 0.0
            else (None if positive_r else 0.0)
        ),
        "pnl_distribution": _quantiles(pnls),
        "r_distribution": _quantiles(rs),
    }


def _group(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get(key)), []).append(row)
    return {name: _summary(items) for name, items in sorted(grouped.items())}


def analyze(
    output: Path,
    expected_trades: int,
    feature_keys: tuple[str, ...],
) -> dict[str, Any]:
    path = output / "closed_scenarios.json"
    records = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    ledger: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        pnl = _money(record.get("realized_pnl"))
        planned = _number(record.get("planned_account_loss"), math.nan)
        actual_r = (
            pnl / planned
            if pnl is not None and math.isfinite(planned) and planned > 0.0
            else None
        )
        diagnostics = record.get("diagnostics") or {}
        row: dict[str, Any] = {
            "trade_index": index,
            "scenario_id": record.get("scenario_id"),
            "symbol": record.get("symbol"),
            "side": record.get("side"),
            "episode_ts": record.get("episode_ts"),
            "close_ts": record.get("ts_event"),
            "entry_reference": record.get("entry_reference"),
            "stop": record.get("stop"),
            "target": record.get("target"),
            "planned_account_loss": planned if math.isfinite(planned) else None,
            "pnl_usdt": pnl,
            "actual_r": actual_r,
            "exit_reason": str(
                record.get("management_exit_reason")
                or "UNTAGGED_BRACKET_OR_ENGINE"
            ),
        }
        for key in feature_keys:
            value = _number(diagnostics.get(key), math.nan)
            row[key] = value if math.isfinite(value) else None
        ledger.append(row)

    contrast: dict[str, Any] = {}
    for key in feature_keys:
        winners = [
            float(row[key])
            for row in ledger
            if row[key] is not None and _number(row["pnl_usdt"]) > 0.0
        ]
        losers = [
            float(row[key])
            for row in ledger
            if row[key] is not None and _number(row["pnl_usdt"]) < 0.0
        ]
        contrast[key] = {
            "winner_mean": sum(winners) / len(winners) if winners else None,
            "loser_mean": sum(losers) / len(losers) if losers else None,
            "winner_distribution": _quantiles(winners),
            "loser_distribution": _quantiles(losers),
        }

    return {
        "ledger_rows": len(ledger),
        "metrics_trade_count": expected_trades,
        "ledger_matches_metrics": len(ledger) == expected_trades,
        "overall": _summary(ledger),
        "by_symbol": _group(ledger, "symbol"),
        "by_side": _group(ledger, "side"),
        "by_exit_reason": _group(ledger, "exit_reason"),
        "feature_contrast": contrast,
        "best_trades": sorted(
            ledger,
            key=lambda row: _number(row["actual_r"], -math.inf),
            reverse=True,
        )[:10],
        "worst_trades": sorted(
            ledger,
            key=lambda row: _number(row["actual_r"], math.inf),
        )[:10],
        "trade_ledger": ledger,
    }


__all__ = ["analyze"]
