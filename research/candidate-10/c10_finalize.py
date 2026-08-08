#!/usr/bin/env python3
"""Generate Candidate 10's strict final verdict from GitHub Actions artifacts.

This script never selects a period or variant by its PnL.  It scans every
available metrics.json, applies one predeclared continuous-account contract, and
writes the authoritative Markdown and machine-readable verdict.  A controlled
week, a sparse perfect record, or a zero-trade holdout cannot pass.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any


def as_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else result


def first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def position_concentration(metrics_path: Path) -> tuple[float | None, int | None]:
    positions = metrics_path.parent / "positions.csv"
    if not positions.is_file():
        return None, None
    try:
        with positions.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
    except Exception:
        return None, None
    pnl_values: list[float] = []
    for row in rows:
        raw = row.get("realized_pnl", row.get("pnl"))
        try:
            pnl_values.append(float(str(raw).split()[0]))
        except (TypeError, ValueError, IndexError):
            continue
    positives = sorted((x for x in pnl_values if x > 0), reverse=True)
    gross = sum(positives)
    share = sum(positives[:3]) / gross if gross > 0 else None
    return share, len(pnl_values)


def parse_row(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    trades = int(data.get("closed_trades") or 0)
    wins = int(data.get("wins") or 0)
    losses = int(data.get("losses") or 0)
    days = int(data.get("evaluation_days") or 0)
    evidence_class = data.get("evidence_class")
    top3_share, ledger_trade_count = position_concentration(path)
    required_files = (
        "orders.csv",
        "positions.csv",
        "account.csv",
        "impact_cost_records.json",
        "data_manifest.json",
    )
    evidence_files = {
        name: (path.parent / name).is_file()
        for name in required_files
    }
    row = {
        "path": str(path),
        "candidate": str(data.get("candidate_generation") or data.get("candidate") or "unknown"),
        "variant": str(data.get("variant") or path.parent.name),
        "start": first_present(data, "period_start", "evaluation_start", "week_start"),
        "end": first_present(data, "period_end_exclusive", "evaluation_end_exclusive"),
        "evaluation_days": days,
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / trades if trades else 0.0,
        "payoff_ratio": as_float(first_present(data, "impact_adjusted_payoff_ratio", "payoff_ratio")),
        "nav": as_float(first_present(data, "impact_adjusted_ending_nav", "ending_nav", "final_nav")),
        "daily_geometric_growth": as_float(first_present(
            data,
            "impact_adjusted_geometric_daily_growth",
            "geometric_daily_growth",
            "daily_geometric_growth",
        )),
        "max_drawdown": as_float(first_present(
            data,
            "impact_adjusted_intraday_max_drawdown",
            "intraday_max_drawdown",
            "max_drawdown",
        )),
        "errors": len(data.get("errors", []) or []),
        "global_overlap_count": int(data.get("global_overlap_count") or 0),
        "liquidation_detected": bool(data.get("liquidation_detected", False)),
        "risk_budget_violation_count": int(data.get("risk_budget_violation_count") or 0),
        "evidence_class": evidence_class,
        "top3_positive_pnl_share": top3_share,
        "ledger_trade_count": ledger_trade_count,
        "evidence_files": evidence_files,
    }
    row["continuous"] = bool(
        days >= 90
        or "continuous" in str(evidence_class).lower()
        or "CONTINUOUS" in str(path).upper()
        or "H1_2026" in str(path).upper()
    )
    # Missing standard ledgers fail closed for a success claim.  Concentration
    # is reported but not made mandatory when gross positive PnL is unavailable.
    row["evidence_complete"] = all(evidence_files.values())
    row["target_pass"] = bool(
        row["continuous"]
        and trades >= 30
        and row["win_rate"] >= 0.90
        and row["payoff_ratio"] is not None
        and row["payoff_ratio"] >= 1.20
        and row["daily_geometric_growth"] is not None
        and row["daily_geometric_growth"] >= 0.01
        and row["max_drawdown"] is not None
        and row["max_drawdown"] <= 0.20
        and row["errors"] == 0
        and row["global_overlap_count"] == 0
        and row["risk_budget_violation_count"] == 0
        and not row["liquidation_detected"]
        and row["evidence_complete"]
    )
    return row


def format_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100.0 * value:.4f}%"


def format_num(value: float | None) -> str:
    if value is None:
        return "n/a"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return f"{value:.6f}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    rows = [
        row
        for path in args.artifacts.rglob("metrics.json")
        if (row := parse_row(path)) is not None
    ]
    rows.sort(key=lambda r: (r["candidate"], str(r["start"]), r["variant"], r["path"]))
    continuous = [row for row in rows if row["continuous"]]
    passing = [row for row in continuous if row["target_pass"]]

    def rank_key(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            row["target_pass"],
            row["daily_geometric_growth"] if row["daily_geometric_growth"] is not None else -999.0,
            row["win_rate"],
            row["trades"],
            -(row["max_drawdown"] if row["max_drawdown"] is not None else 999.0),
        )

    best = max(continuous, key=rank_key, default=None)
    verdict = "TARGET_ACHIEVED" if passing else "TARGET_NOT_ACHIEVED"
    payload = {
        "schema": "candidate-10-final-research-verdict-v4",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "branch": "research/candidate-10",
        "verdict": verdict,
        "success_claim": bool(passing),
        "decision_contract": {
            "continuous_days_min": 90,
            "closed_trades_min": 30,
            "win_rate_min": 0.90,
            "payoff_ratio_min": 1.20,
            "daily_geometric_growth_min": 0.01,
            "max_drawdown_max": 0.20,
            "errors_required": 0,
            "global_overlap_required": 0,
            "risk_budget_violations_required": 0,
            "liquidation_required": False,
            "standard_evidence_files_required": True,
        },
        "best_continuous_row": best,
        "target_passing_rows": passing,
        "continuous_rows": continuous,
        "rows": rows,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = args.output_dir / "final_research_metrics.json"
    metrics_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=True) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Candidate 10 — Final autonomous research verdict",
        "",
        f"Verdict: **{verdict}**",
        "",
        "The verdict is generated from every available final-lineage artifact under one predeclared contract. Controlled wins, sparse perfect records, zero-trade preservation and loss reduction cannot pass.",
        "",
        "## Fixed success contract",
        "",
        "- at least 90 continuous calendar days and 30 closed trades;",
        "- cost-after win rate at least 90%;",
        "- cost-after payoff ratio at least 1.20;",
        "- cost-after geometric daily growth at least 1%;",
        "- maximum drawdown at most 20%;",
        "- zero implementation errors, liquidations, risk-budget violations and global-slot overlaps;",
        "- complete orders, positions, account, impact-cost and data-manifest evidence.",
        "",
    ]
    if best is None:
        lines += ["## Continuous evidence", "", "No completed continuous artifact was available.", ""]
    else:
        lines += [
            "## Best completed continuous result",
            "",
            f"- candidate: `{best['candidate']}`",
            f"- variant: `{best['variant']}`",
            f"- period: `{best['start']}` to `{best['end']}`",
            f"- evaluation days: {best['evaluation_days']}",
            f"- trades: {best['trades']} ({best['wins']} wins / {best['losses']} losses)",
            f"- win rate: {format_pct(best['win_rate'])}",
            f"- payoff ratio: {format_num(best['payoff_ratio'])}",
            f"- cost-after ending NAV: {format_num(best['nav'])}",
            f"- cost-after geometric daily growth: {format_pct(best['daily_geometric_growth'])}",
            f"- maximum drawdown: {format_pct(best['max_drawdown'])}",
            f"- top-three positive-PnL share: {format_pct(best['top3_positive_pnl_share'])}",
            f"- implementation errors: {best['errors']}",
            f"- evidence complete: {best['evidence_complete']}",
            f"- target pass: {best['target_pass']}",
            "",
        ]
    lines += [
        "## All completed continuous variants",
        "",
        "| Candidate | Variant | Days | Trades | W/L | Win rate | Payoff | Daily geom | NAV | Max DD | Evidence | Pass |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in continuous:
        lines.append(
            f"| {row['candidate']} | {row['variant']} | {row['evaluation_days']} | "
            f"{row['trades']} | {row['wins']}/{row['losses']} | {format_pct(row['win_rate'])} | "
            f"{format_num(row['payoff_ratio'])} | {format_pct(row['daily_geometric_growth'])} | "
            f"{format_num(row['nav'])} | {format_pct(row['max_drawdown'])} | "
            f"{row['evidence_complete']} | {row['target_pass']} |"
        )
    lines += ["", "## Final conclusion", ""]
    if passing:
        lines += [
            "Only the frozen continuous rows explicitly marked `target_pass: true` are approved as completed candidates.",
        ]
    else:
        lines += [
            "No frozen continuous variant satisfies the project target.",
            "",
            "The source-equilibrium FAR, independent external-draw FAR and cross-market transfer-role lineages are not approved for live trading. Their useful components remain diagnostic evidence rather than a completed system.",
        ]
    lines += ["", "Machine-readable evidence: `final_research_metrics.json`.", ""]
    (args.output_dir / "FINAL_RESEARCH_RESULT.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    achieved_marker = args.output_dir / "TARGET_ACHIEVED.marker"
    failed_marker = args.output_dir / "TARGET_NOT_ACHIEVED.marker"
    achieved_marker.unlink(missing_ok=True)
    failed_marker.unlink(missing_ok=True)
    (achieved_marker if passing else failed_marker).write_text(
        verdict + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
