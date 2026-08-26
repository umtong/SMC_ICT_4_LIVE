#!/usr/bin/env python3
"""Select regime-stable causal mechanism cells from OOF short decisions.

This is not a generic scorecard.  It asks which concrete market mechanisms keep
working when the date changes: accepted first retest, initiative mitigation,
locally owned reclaim and structural flip retest.  Selection uses development
outcomes only; the chosen cells are then routed unchanged through fresh periods
in one account.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path
import sys
from typing import Any, Sequence

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import rich_causal_action_router as core  # noqa: E402

CELL_LEVELS: tuple[tuple[str, ...], ...] = (
    ("scenario_family", "geometry_class", "route_class", "rr_band", "auction_phase"),
    ("scenario_family", "geometry_class", "route_class", "rr_band"),
    ("scenario_family", "geometry_class", "rr_band"),
    ("scenario_family", "rr_band"),
    ("scenario_family",),
)


def resolved(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["net_r_num"] = pd.to_numeric(
        out.get("net_r_num", out.get("net_r")), errors="coerce"
    )
    out = out[out.net_r_num.notna()].copy()
    out["win"] = core.text(out, "outcome").str.upper().eq("TARGET_FIRST")
    return out


def cell_key(row: pd.Series, columns: Sequence[str]) -> tuple[str, ...]:
    return tuple(str(row.get(column, "UNKNOWN")) for column in columns)


def group_evidence(
    development: pd.DataFrame,
    columns: Sequence[str],
) -> dict[tuple[str, ...], dict[str, Any]]:
    output: dict[tuple[str, ...], dict[str, Any]] = {}
    for raw_key, group in development.groupby(list(columns), dropna=False, sort=False):
        key = raw_key if isinstance(raw_key, tuple) else (raw_key,)
        key = tuple(str(value) for value in key)
        if len(group) < 12:
            continue
        wins = int(group.win.sum())
        alpha = wins + 6.0
        beta = len(group) - wins + 6.0
        posterior = alpha / (alpha + beta)
        posterior_std = math.sqrt(
            alpha * beta / max((alpha + beta) ** 2 * (alpha + beta + 1.0), core.EPS)
        )
        period_rows: list[dict[str, Any]] = []
        for period, part in group.groupby("period", sort=True):
            if len(part) < 3:
                continue
            period_rows.append(
                {
                    "period": str(period),
                    "trades": int(len(part)),
                    "target_rate": float(part.win.mean()),
                    "mean_r": float(part.net_r_num.mean()),
                }
            )
        means = [row["mean_r"] for row in period_rows]
        rates = [row["target_rate"] for row in period_rows]
        output[key] = {
            "columns": list(columns),
            "key": list(key),
            "trades": int(len(group)),
            "periods": int(len(period_rows)),
            "posterior_target": float(posterior),
            "posterior_lower": float(max(0.01, posterior - 0.65 * posterior_std)),
            "mean_r": float(group.net_r_num.mean()),
            "period_q20_mean_r": float(np.quantile(means, 0.20)) if means else -1.0,
            "median_period_target_rate": float(np.median(rates)) if rates else 0.0,
            "period_rows": period_rows,
        }
    return output


def select_cells(development: pd.DataFrame) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tables = [(columns, group_evidence(development, columns)) for columns in CELL_LEVELS]
    chosen: list[dict[str, Any]] = []
    seen_families: set[str] = set()

    # Prefer the deepest semantic explanation with support in several dates.
    candidates: list[dict[str, Any]] = []
    for _, table in tables:
        for item in table.values():
            if item["periods"] < 2 or item["trades"] < 16:
                continue
            if item["posterior_lower"] <= 0.50:
                continue
            if item["mean_r"] <= 0.0:
                continue
            stability = (
                1.10 * item["period_q20_mean_r"]
                + 0.65 * (item["median_period_target_rate"] - 0.50)
                + 0.10 * math.log1p(item["trades"])
                + 0.04 * len(item["columns"])
            )
            item = dict(item)
            item["stability_score"] = float(stability)
            candidates.append(item)
    candidates.sort(
        key=lambda item: (
            item["stability_score"], len(item["columns"]), item["trades"]
        ),
        reverse=True,
    )

    # Keep non-redundant cells.  A more specific cell owns its observations;
    # broader backoffs are added only when they bring a different scenario.
    signatures: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
    for item in candidates:
        signature = (tuple(item["columns"]), tuple(item["key"]))
        if signature in signatures:
            continue
        family = item["key"][item["columns"].index("scenario_family")]
        if family == "OTHER":
            continue
        if family in seen_families and len(item["columns"]) <= 2:
            continue
        signatures.add(signature)
        seen_families.add(family)
        chosen.append(item)

    return chosen, {
        "candidate_cells": candidates,
        "selected_cells": chosen,
    }


def matching_mask(frame: pd.DataFrame, cells: list[dict[str, Any]]) -> pd.Series:
    mask = pd.Series(False, index=frame.index)
    for cell in cells:
        part = pd.Series(True, index=frame.index)
        for column, value in zip(cell["columns"], cell["key"], strict=True):
            part &= frame[column].astype(str).eq(str(value))
        mask |= part
    return mask


def route_mask(frame: pd.DataFrame, mask: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    work = frame.copy()
    work["evidence_supported"] = (
        work.evidence_supported.fillna(False)
        & mask.fillna(False)
    )
    families = set(work.loc[work.evidence_supported, "scenario_family"].astype(str))
    return core.route_account(work, families)


def run(scored_path: Path, output: Path) -> dict[str, Any]:
    scored = pd.read_csv(scored_path, low_memory=False)
    if "order_time" not in scored:
        scored["order_time"] = pd.to_datetime(
            pd.to_numeric(scored.order_time_ns, errors="coerce"),
            unit="ns", utc=True, errors="coerce",
        )
    else:
        scored["order_time"] = pd.to_datetime(scored.order_time, utc=True, errors="coerce")
    if "terminal_time" in scored:
        scored["terminal_time"] = pd.to_datetime(
            scored.terminal_time, utc=True, errors="coerce"
        )
    else:
        scored["terminal_time"] = pd.to_datetime(
            pd.to_numeric(scored.get("order_terminal_time_ns"), errors="coerce"),
            unit="ns", utc=True, errors="coerce",
        )
    development = resolved(scored[scored.role.astype(str).eq("dev")])
    cells, clinic = select_cells(development)
    mask = matching_mask(scored, cells)
    orders, trades = route_mask(scored, mask)
    period_days = core.infer_period_days(scored)
    dev = trades[trades.role.astype(str).eq("dev")]
    fresh = trades[trades.role.astype(str).eq("fresh")]
    summary = {
        "policy": "ML_K_REGIME_STABLE_CAUSAL_MECHANISMS",
        "selection_uses_fresh_outcomes": False,
        "selected_cells": cells,
        "development": core.metrics(dev, period_days),
        "fresh": core.metrics(fresh, period_days),
        "development_by_period": core.grouped(dev, "period"),
        "fresh_by_period": core.grouped(fresh, "period"),
        "fresh_by_family": core.grouped(fresh, "scenario_family"),
        "fresh_by_symbol": core.grouped(fresh, "symbol"),
        "cell_clinic": clinic,
    }
    output.mkdir(parents=True, exist_ok=True)
    orders.to_csv(output / "selected_orders.csv", index=False)
    trades.to_csv(output / "closed_trades.csv", index=False)
    fresh.to_csv(output / "fresh_closed_trades.csv", index=False)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scored", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.scored, args.output)


if __name__ == "__main__":
    main()
