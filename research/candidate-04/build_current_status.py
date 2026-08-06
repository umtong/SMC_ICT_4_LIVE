#!/usr/bin/env python3
"""Build a concise current-status report from committed research evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


def load(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def percent(value: Any) -> str:
    try:
        return f"{100.0 * float(value):+.3f}%"
    except (TypeError, ValueError):
        return "—"


def bool_text(value: Any) -> str:
    return str(bool(value)).lower()


def candidate_name(value: dict[str, Any], fallback: str) -> str:
    return str(
        value.get("candidate")
        or (value.get("summary") or {}).get("candidate")
        or fallback
    )


def walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def metric_rows(record: dict[str, Any], label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in walk_dicts(record):
        if not isinstance(item, dict):
            continue
        if not all(key in item for key in ("trades", "total_return")):
            continue
        key = (
            item.get("stage"),
            item.get("evaluation_start"),
            item.get("evaluation_end"),
            item.get("trades"),
            item.get("wins"),
            item.get("total_return"),
            item.get("geometric_daily_growth"),
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "candidate": candidate_name(item, label),
                "stage": item.get("stage") or item.get("matrix") or "recorded",
                "period": (
                    f"{item.get('evaluation_start')}..{item.get('evaluation_end')}"
                    if item.get("evaluation_start") and item.get("evaluation_end")
                    else "—"
                ),
                "trades": item.get("trades"),
                "wins": item.get("wins"),
                "win_rate": item.get("win_rate"),
                "total_return": item.get("total_return"),
                "geometric_daily_growth": item.get("geometric_daily_growth"),
                "max_drawdown": item.get("max_drawdown"),
                "pass": item.get("candidate_pass")
                if "candidate_pass" in item
                else item.get("project_target_reached"),
            }
        )
    return rows


def decision_text(record: dict[str, Any]) -> str:
    primary = record.get("primary_evidence")
    if isinstance(primary, dict):
        value = primary.get("value")
        if isinstance(value, dict):
            return str(
                value.get("decision")
                or value.get("status")
                or value.get("stage")
                or record.get("failure_classification")
            )
    return str(
        record.get("decision")
        or record.get("status")
        or record.get("stage")
        or record.get("failure_classification")
        or "recorded"
    )


def target_reached(record: dict[str, Any]) -> bool:
    return any(
        bool(item.get(key))
        for item in walk_dicts(record)
        for key in (
            "project_target_reached",
            "project_development_target_reached",
        )
        if key in item
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    run_paths = sorted((args.root / "run-evidence").glob("*.json"))
    legacy_paths = sorted(args.root.glob("evidence-v3*.json"))
    records: list[tuple[Path, dict[str, Any]]] = []
    for path in [*run_paths, *legacy_paths]:
        value = load(path)
        if value is not None:
            records.append((path, value))

    lines = [
        "# Candidate 04 — Current Reproducible Status",
        "",
        "Generated from committed run-specific and legacy evidence. Official fills, costs, positions, PnL and NAV are sourced from NautilusTrader artifacts; this report does not recalculate them.",
        "",
        "## Run decisions",
        "",
        "| Evidence | Workflow / candidate | Conclusion | Decision | Project target reached |",
        "|---|---|---|---|---:|",
    ]
    for path, value in records:
        label = str(value.get("workflow") or candidate_name(value, path.stem))
        conclusion = str(value.get("workflow_conclusion") or "recorded")
        lines.append(
            f"| `{path.name}` | {label} | {conclusion} | {decision_text(value)} | {bool_text(target_reached(value))} |"
        )

    rows: list[dict[str, Any]] = []
    for path, value in records:
        rows.extend(metric_rows(value, path.stem))
    lines.extend(
        [
            "",
            "## NautilusTrader metrics found in evidence",
            "",
            "| Candidate | Stage | Period | Trades | Wins | Win rate | Return | Geometric daily | Max DD | Pass |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    if rows:
        for row in rows:
            lines.append(
                f"| {row['candidate']} | {row['stage']} | {row['period']} | "
                f"{row['trades']} | {row['wins']} | {percent(row['win_rate'])} | "
                f"{percent(row['total_return'])} | {percent(row['geometric_daily_growth'])} | "
                f"{percent(row['max_drawdown'])} | {row['pass']} |"
            )
    else:
        lines.append("| — | — | — | — | — | — | — | — | — | — |")

    lines.extend(
        [
            "",
            "## Fixed contracts",
            "",
            "- BTC-first research; BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT are the only tradable instruments.",
            "- Across all instruments, pending new entries plus open positions are at most one.",
            "- Quantity is based on current account NAV and all-in planned stop loss; maximum planned loss is 3% NAV.",
            "- No custom matching/PnL engine and no arbitrary nominal or leverage cap.",
            "- Candidates advance sequentially: development weeks, predeclared unopened weeks, then long evaluation.",
            "- Implementation failures are rerun on the identical week after a controlled fix; economic failures receive one core-mechanism ablation.",
            "",
        ]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
