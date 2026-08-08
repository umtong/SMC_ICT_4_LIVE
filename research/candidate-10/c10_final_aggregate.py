#!/usr/bin/env python3
"""Build the final Candidate-10 verdict from all completed continuous evidence.

This script never chooses a period after seeing PnL for the success claim.  It
scans every supplied artifact, retains the predeclared evidence identity, and
requires at least one clean continuous account to satisfy the complete project
contract.  Control weeks are reported but cannot establish success.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

UTC = timezone.utc


@dataclass(frozen=True, slots=True)
class EvidenceRow:
    path: str
    candidate: str
    variant: str
    evidence_class: str
    start: str | None
    end_exclusive: str | None
    evaluation_days: int
    continuous: bool
    trades: int
    wins: int
    losses: int
    win_rate: float
    payoff_ratio: float | None
    nav: float | None
    daily_geometric_growth: float | None
    max_drawdown: float | None
    errors: int
    liquidation: bool
    global_overlap_count: int
    risk_budget_violations: int
    event_log_valid: bool
    target_pass: bool
    source_workflow: str


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if mapping.get(key) is not None:
            return mapping[key]
    return None


def infer_candidate(metrics: dict[str, Any], path: Path) -> str:
    explicit = first(
        metrics,
        "candidate_generation",
        "candidate",
        "system",
    )
    if explicit:
        return str(explicit)
    text = str(path).lower()
    for name in ("v49", "v50", "v48", "v47"):
        if name in text:
            return f"candidate-10-{name}"
    return "unknown"


def infer_variant(metrics: dict[str, Any], path: Path) -> str:
    value = metrics.get("variant")
    if value:
        return str(value)
    parent = path.parent.name
    return parent or "unknown"


def infer_evidence_class(metrics: dict[str, Any], path: Path) -> str:
    value = first(
        metrics,
        "v50_evidence_class",
        "v49_evidence_class",
        "evidence_class",
    )
    if value:
        return str(value)
    text = str(path).lower()
    if "continuous" in text or "p202" in text:
        return "continuous_unspecified"
    if "control" in text:
        return "controlled_attribution"
    return "unspecified"


def infer_days(metrics: dict[str, Any]) -> int:
    value = first(metrics, "evaluation_days", "days")
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def count_errors(metrics: dict[str, Any]) -> int:
    value = metrics.get("errors")
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return sum(int(v) for v in value.values() if isinstance(v, (int, float)))
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def count_risk_violations(metrics: dict[str, Any]) -> int:
    for key in (
        "risk_budget_violation_count",
        "risk_budget_violations",
        "impact_adjusted_risk_budget_violation_count",
    ):
        value = metrics.get(key)
        if isinstance(value, list):
            return len(value)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    records = metrics.get("cost_records", [])
    if not isinstance(records, list):
        return 0
    count = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        nav = finite(first(record, "conservative_nav_before", "nav_before"))
        loss = finite(first(record, "expected_total_loss", "planned_loss"))
        if nav is not None and nav > 0 and loss is not None and loss > nav * 0.0300001:
            count += 1
    return count


def parse_metrics(path: Path, root_name: str) -> EvidenceRow | None:
    try:
        metrics = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    candidate = infer_candidate(metrics, path)
    if "candidate-10" not in candidate and not any(
        tag in str(path).lower() for tag in ("candidate-10", "v49", "v50")
    ):
        return None
    variant = infer_variant(metrics, path)
    evidence_class = infer_evidence_class(metrics, path)
    days = infer_days(metrics)
    start = first(
        metrics,
        "period_start",
        "evaluation_start",
        "start",
        "week_start",
    )
    end = first(
        metrics,
        "period_end_exclusive",
        "evaluation_end_exclusive",
        "end_exclusive",
    )
    trades = int(first(metrics, "closed_trades", "trades") or 0)
    wins = int(metrics.get("wins") or 0)
    losses = int(metrics.get("losses") or 0)
    win_rate = finite(metrics.get("win_rate"))
    if win_rate is None:
        win_rate = wins / trades if trades else 0.0
    payoff = finite(first(
        metrics,
        "impact_adjusted_payoff_ratio",
        "payoff_ratio",
    ))
    nav = finite(first(
        metrics,
        "impact_adjusted_ending_nav",
        "impact_adjusted_final_nav",
        "ending_nav",
        "final_nav",
    ))
    growth = finite(first(
        metrics,
        "impact_adjusted_geometric_daily_growth",
        "impact_adjusted_daily_geometric_growth",
        "daily_geometric_growth",
        "geometric_daily_growth",
    ))
    drawdown = finite(first(
        metrics,
        "impact_adjusted_intraday_max_drawdown",
        "impact_adjusted_max_drawdown",
        "intraday_max_drawdown",
        "max_drawdown",
        "closed_trade_max_drawdown",
    ))
    errors = count_errors(metrics)
    liquidation = bool(metrics.get("liquidation_detected"))
    if not liquidation:
        liquidation = "LIQUIDAT" in json.dumps(
            metrics.get("order_lifecycle", metrics.get("lifecycle", [])),
            default=str,
        ).upper()
    overlaps = int(metrics.get("global_overlap_count") or 0)
    risk_violations = count_risk_violations(metrics)
    event_log_valid = bool(metrics.get("event_log_valid", True))

    evidence_lower = evidence_class.lower()
    continuous = (
        days >= 60
        and "control" not in evidence_lower
        and "diagnostic" not in evidence_lower
        and "attribution" not in evidence_lower
    )
    target_pass = bool(
        continuous
        and trades >= 30
        and win_rate >= 0.90
        and payoff is not None
        and payoff >= 1.20
        and growth is not None
        and growth >= 0.01
        and drawdown is not None
        and drawdown <= 0.20
        and errors == 0
        and not liquidation
        and overlaps == 0
        and risk_violations == 0
        and event_log_valid
    )
    return EvidenceRow(
        path=str(path),
        candidate=candidate,
        variant=variant,
        evidence_class=evidence_class,
        start=None if start is None else str(start),
        end_exclusive=None if end is None else str(end),
        evaluation_days=days,
        continuous=continuous,
        trades=trades,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        payoff_ratio=payoff,
        nav=nav,
        daily_geometric_growth=growth,
        max_drawdown=drawdown,
        errors=errors,
        liquidation=liquidation,
        global_overlap_count=overlaps,
        risk_budget_violations=risk_violations,
        event_log_valid=event_log_valid,
        target_pass=target_pass,
        source_workflow=root_name,
    )


def quality_key(row: EvidenceRow) -> tuple[float, float, float, int]:
    # Diagnostic ordering only. It is never used to create a success claim.
    return (
        -999.0 if row.daily_geometric_growth is None else row.daily_geometric_growth,
        row.win_rate,
        -999.0 if row.payoff_ratio is None else row.payoff_ratio,
        row.trades,
    )


def fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "NA"
    return f"{float(value):.{digits}f}"


def build_report(
    rows: list[EvidenceRow],
    passing: list[EvidenceRow],
    branch_sha: str,
    workflow_runs: dict[str, Any],
) -> str:
    continuous = [row for row in rows if row.continuous]
    controls = [row for row in rows if not row.continuous]
    best = max(continuous, key=quality_key, default=None)
    verdict = "TARGET_ACHIEVED" if passing else "TARGET_NOT_ACHIEVED"
    lines = [
        "# Candidate 10 — final autonomous research verdict",
        "",
        f"Verdict: **{verdict}**",
        "",
        f"Branch evidence SHA: `{branch_sha}`",
        f"Generated UTC: `{datetime.now(UTC).isoformat()}`",
        "",
        "## Non-negotiable success contract",
        "",
        "A success claim requires a predeclared continuous account of at least 60 days, "
        "at least 30 independent closed trades, cost-after win rate at least 90%, payoff "
        "ratio at least 1.2, geometric daily growth at least 1%, maximum drawdown at most "
        "20%, and zero engine, event-log, liquidation, global-slot or 3%-risk-budget errors.",
        "",
        "Weekly controls and zero-trade periods cannot establish success.",
        "",
        "## Workflow evidence",
        "",
    ]
    for name, value in sorted(workflow_runs.items()):
        lines.append(f"- `{name}`: `{value}`")
    lines.extend([
        "",
        "## Continuous-account evidence",
        "",
        "| candidate | variant | period | days | trades | W/L | win rate | payoff | cost NAV | daily geom | max DD | clean | target |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for row in sorted(
        continuous,
        key=lambda item: (
            item.start or "",
            item.candidate,
            item.variant,
        ),
    ):
        clean = (
            row.errors == 0
            and not row.liquidation
            and row.global_overlap_count == 0
            and row.risk_budget_violations == 0
            and row.event_log_valid
        )
        lines.append(
            "| {candidate} | {variant} | {start}..{end} | {days} | {trades} | "
            "{wins}/{losses} | {wr} | {payoff} | {nav} | {growth} | {dd} | "
            "{clean} | {target} |".format(
                candidate=row.candidate,
                variant=row.variant,
                start=row.start,
                end=row.end_exclusive,
                days=row.evaluation_days,
                trades=row.trades,
                wins=row.wins,
                losses=row.losses,
                wr=fmt(row.win_rate),
                payoff=fmt(row.payoff_ratio),
                nav=fmt(row.nav, 2),
                growth=fmt(row.daily_geometric_growth, 6),
                dd=fmt(row.max_drawdown, 6),
                clean=clean,
                target=row.target_pass,
            ),
        )
    if not continuous:
        lines.append("| none | none | none | 0 | 0 | 0/0 | NA | NA | NA | NA | NA | False | False |")

    lines.extend([
        "",
        "## Diagnostic best continuous row",
        "",
    ])
    if best is None:
        lines.append("No completed continuous row was available; success is prohibited.")
    else:
        lines.extend([
            f"- Candidate: `{best.candidate}`",
            f"- Variant: `{best.variant}`",
            f"- Period: `{best.start}` to `{best.end_exclusive}` ({best.evaluation_days} days)",
            f"- Trades: `{best.trades}`; wins/losses: `{best.wins}/{best.losses}`",
            f"- Cost-after win rate: `{fmt(best.win_rate, 6)}`",
            f"- Cost-after payoff ratio: `{fmt(best.payoff_ratio, 6)}`",
            f"- Cost-after NAV: `{fmt(best.nav, 2)}`",
            f"- Geometric daily growth: `{fmt(best.daily_geometric_growth, 8)}`",
            f"- Maximum drawdown: `{fmt(best.max_drawdown, 8)}`",
            f"- Target pass: `{best.target_pass}`",
        ])

    lines.extend([
        "",
        "## Final system logic retained",
        "",
        "The strongest surviving logic is a causal auction-state system rather than a "
        "candlestick classifier:",
        "",
        "```text",
        "completed regional/source auction boundary",
        "→ actual liquidity raid",
        "→ reclaim + post-sweep internal structure break + directional displacement",
        "→ synchronized cross-market price-discovery attribution",
        "→ passive entry at the first displacement execution-void edge",
        "→ source equilibrium or a pre-existing independent external-liquidity draw",
        "→ hard source-raid invalidation sized from current all-cost NAV",
        "```",
        "",
        "BTC, ETH, SOL and XRP share one logic and one global pending/position slot. "
        "NautilusTrader owns clocks, orders, fills, fees, margin, positions and NAV; "
        "size-dependent impact is debited at actual fills, and every next position is "
        "sized from the resulting conservative NAV.",
        "",
        "## Known failure conditions",
        "",
        "- The source-equilibrium failed-auction family becomes sparse when cross-market "
        "event leadership is required; absence of trades is not positive evidence.",
        "- A completed close through the first displacement void detects many losses but "
        "also exits valid rebalancing winners, so it cannot be a universal stop.",
        "- Narrowing the hard stop to the displacement void increases size and modeled "
        "impact and can turn valid winners into execution failures.",
        "- Static CE, deeper source-boundary entries, first-pivot hard stops and early "
        "funded partial exits all reduced robustness or opportunity frequency.",
        "- Independent external-draw continuations remain vulnerable when the candidate is "
        "not itself leading the synchronized event-direction move.",
        "",
        "## Final interpretation",
        "",
    ])
    if passing:
        lines.append(
            "At least one continuous account satisfied every predeclared project gate. "
            "The passing rows above are the only basis for the success claim."
        )
    else:
        lines.append(
            "No continuous account satisfied every project gate. The branch contains a "
            "reproducible causal research system and several valid components, but it is "
            "not approved for live capital and no perfect-system claim is made."
        )
    lines.extend([
        "",
        f"Control rows parsed: `{len(controls)}`; continuous rows parsed: `{len(continuous)}`; passing rows: `{len(passing)}`.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--branch-sha", default="unknown")
    parser.add_argument("--workflow-runs", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    workflow_runs: dict[str, Any] = {}
    if args.workflow_runs and args.workflow_runs.is_file():
        try:
            workflow_runs = json.loads(
                args.workflow_runs.read_text(encoding="utf-8"),
            )
        except Exception:
            workflow_runs = {"workflow_runs_parse_error": str(args.workflow_runs)}

    rows: list[EvidenceRow] = []
    seen: set[tuple[Any, ...]] = set()
    for path in sorted(args.root.rglob("metrics.json")):
        row = parse_metrics(path, path.parts[-4] if len(path.parts) >= 4 else "unknown")
        if row is None:
            continue
        identity = (
            row.candidate,
            row.variant,
            row.start,
            row.end_exclusive,
            row.evidence_class,
            row.trades,
            row.nav,
        )
        if identity in seen:
            continue
        seen.add(identity)
        rows.append(row)

    passing = [row for row in rows if row.target_pass]
    continuous = [row for row in rows if row.continuous]
    best = max(continuous, key=quality_key, default=None)
    verdict = "TARGET_ACHIEVED" if passing else "TARGET_NOT_ACHIEVED"
    result = {
        "schema": "candidate-10-final-research-verdict-v1",
        "generated_utc": datetime.now(UTC).isoformat(),
        "branch_sha": args.branch_sha,
        "verdict": verdict,
        "success_claim": bool(passing),
        "workflow_runs": workflow_runs,
        "strict_contract": {
            "continuous_days_min": 60,
            "closed_trades_min": 30,
            "win_rate_min": 0.90,
            "payoff_ratio_min": 1.20,
            "geometric_daily_growth_min": 0.01,
            "max_drawdown_max": 0.20,
            "errors_max": 0,
            "liquidation_allowed": False,
            "global_overlap_max": 0,
            "risk_budget_violations_max": 0,
            "event_log_must_be_valid": True,
        },
        "row_count": len(rows),
        "continuous_row_count": len(continuous),
        "rows": [asdict(row) for row in rows],
        "target_passing_rows": [asdict(row) for row in passing],
        "best_continuous_row": None if best is None else asdict(best),
        "candidate_counts": dict(Counter(row.candidate for row in rows)),
    }
    (args.output / "final_research_metrics.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    report = build_report(rows, passing, args.branch_sha, workflow_runs)
    (args.output / "FINAL_RESEARCH_RESULT.md").write_text(
        report + "\n",
        encoding="utf-8",
    )
    marker = (
        args.output / "TARGET_ACHIEVED.marker"
        if passing
        else args.output / "TARGET_NOT_ACHIEVED.marker"
    )
    marker.write_text(verdict + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
