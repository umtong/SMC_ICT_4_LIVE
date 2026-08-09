#!/usr/bin/env python3
"""Build compact trustworthy CI evidence for end-to-end research workflows."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


FINAL_SHARED_PASS = "PROJECT_ONE_ACCOUNT_FOUR_SYMBOL_LONG_2024_2026H1_GATE_PASSED"


def read_text(path: Path, default: str) -> str:
    return path.read_text(encoding="utf-8", errors="replace").strip() if path.exists() else default


def tail(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]


def load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def finalize_btc(
    *,
    root: Path,
    summary_path: Path,
    winner_path: Path,
    source_commit: str,
    workflow_run_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    verification = read_text(root / "verification_status.txt", "missing")
    research_status = read_text(root / "research_status.txt", "not_run")
    summary = load(summary_path)
    winner = load(winner_path)
    if summary is not None and winner is not None:
        summary["verification_status"] = verification
        summary["research_process_status"] = research_status
    elif verification != "exit_code=0":
        summary = {
            "schema": "candidate-05-validated-btc-research-v3",
            "source_commit": source_commit,
            "workflow_run_id": workflow_run_id,
            "classification": "IMPLEMENTATION_ERROR_BTC_STATIC_OR_CONTRACT",
            "qualified": [],
            "winner": None,
            "verification_status": verification,
            "verification_log_tail": tail(root / "verification.log", 280),
            "research_process_status": research_status,
            "next_action": "Repair the static or contract error without changing hypotheses or ranges, then rerun the latest workflow.",
        }
        winner = {
            "schema": "candidate-05-validated-btc-winner-v3",
            "classification": "NO_VALIDATED_BTC_WINNER",
            "winner": None,
            "source_commit": source_commit,
            "workflow_run_id": workflow_run_id,
            "research_classification": summary["classification"],
            "next_action": summary["next_action"],
        }
    else:
        summary = {
            "schema": "candidate-05-validated-btc-research-v3",
            "source_commit": source_commit,
            "workflow_run_id": workflow_run_id,
            "classification": "IMPLEMENTATION_ERROR_BTC_RESEARCH_RUNTIME",
            "qualified": [],
            "winner": None,
            "verification_status": verification,
            "research_process_status": research_status,
            "research_log_tail": tail(root / "research_console.log", 340),
            "next_action": "Repair the research runtime under variable control and rerun the identical frozen stage.",
        }
        winner = {
            "schema": "candidate-05-validated-btc-winner-v3",
            "classification": "NO_VALIDATED_BTC_WINNER",
            "winner": None,
            "source_commit": source_commit,
            "workflow_run_id": workflow_run_id,
            "research_classification": summary["classification"],
            "next_action": summary["next_action"],
        }
    write(summary_path, summary)
    write(winner_path, winner)
    summary_path.with_suffix(".md").write_text(
        "\n".join(
            [
                "# Candidate 05 Exact-Control BTC Research v3",
                "",
                f"- Classification: `{summary.get('classification')}`",
                f"- Winner: `{summary.get('winner')}`",
                f"- Verification: `{summary.get('verification_status')}`",
                f"- Research process: `{summary.get('research_process_status')}`",
                "",
                "## Next action",
                "",
                str(summary.get("next_action")),
                "",
            ],
        ),
        encoding="utf-8",
    )
    return summary, winner


def finalize_shared(
    *,
    root: Path,
    shared_path: Path,
    source_commit: str,
    workflow_run_id: str,
) -> dict[str, Any]:
    verification = read_text(root / "verification_status.txt", "missing")
    research_status = read_text(root / "research_status.txt", "not_run")
    shared = load(shared_path)
    if shared is not None:
        shared["verification_status"] = verification
        shared["research_process_status"] = research_status
    elif verification != "exit_code=0":
        shared = {
            "schema": "candidate-05-shared-account-research-v3",
            "source_commit": source_commit,
            "workflow_run_id": workflow_run_id,
            "classification": "IMPLEMENTATION_ERROR_SHARED_STATIC_OR_CONTRACT",
            "winner": None,
            "verification_status": verification,
            "verification_log_tail": tail(root / "verification.log", 280),
            "research_process_status": research_status,
            "next_action": "Repair shared-account implementation without changing strategy logic or ranges, then rerun the identical stage.",
        }
    else:
        shared = {
            "schema": "candidate-05-shared-account-research-v3",
            "source_commit": source_commit,
            "workflow_run_id": workflow_run_id,
            "classification": "IMPLEMENTATION_ERROR_SHARED_RUNTIME",
            "winner": None,
            "verification_status": verification,
            "research_process_status": research_status,
            "research_log_tail": tail(root / "research_console.log", 360),
            "next_action": "Repair shared-account runtime under variable control and rerun the identical frozen stage.",
        }
    write(shared_path, shared)
    return shared


def combine(
    *,
    btc_summary: dict[str, Any],
    btc_winner: dict[str, Any],
    shared: dict[str, Any],
    output: Path,
    source_commit: str,
    workflow_run_id: str,
) -> dict[str, Any]:
    if shared.get("classification") == FINAL_SHARED_PASS:
        classification = "PROJECT_GOAL_REACHED_ONE_ACCOUNT_FOUR_SYMBOLS"
        winner = shared.get("winner")
        next_action = shared.get("next_action")
    elif btc_winner.get("classification") != "VALIDATED_BTC_WINNER_RESOLVED":
        classification = btc_summary.get("classification")
        winner = None
        next_action = btc_summary.get("next_action")
    else:
        classification = shared.get("classification")
        winner = None
        next_action = shared.get("next_action")
    result = {
        "schema": "candidate-05-end-to-end-research-v4",
        "source_commit": source_commit,
        "workflow_run_id": workflow_run_id,
        "classification": classification,
        "winner": winner,
        "btc": btc_summary,
        "validated_btc_winner": btc_winner,
        "shared_account": shared,
        "next_action": next_action,
        "project_contract": {
            "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
            "engine": "NautilusTrader only",
            "one_account": True,
            "risk_fraction": 0.03,
            "global_constraint": "unfilled new-entry intents plus open positions <= 1",
            "whole_period_geometric_daily_growth_goal": 0.01,
            "final_evaluation_start": "2024-01-01",
            "final_evaluation_end": "2026-06-30",
            "final_calendar_days": 912,
            "ninety_one_days_is_promotion_only": True,
        },
    }
    write(output, result)
    lines = [
        "# Candidate 05 End-to-End Research",
        "",
        f"- Final classification: `{result.get('classification')}`",
        f"- Final winner: `{result.get('winner')}`",
        f"- BTC classification: `{btc_summary.get('classification')}`",
        f"- BTC winner state: `{btc_winner.get('classification')}`",
        f"- Shared-account classification: `{shared.get('classification')}`",
        "",
        "A 91-day result is a promotion screen only. Project success requires the 912-day 2024-01-01 through 2026-06-30 shared-account result.",
        "",
        "## Next action",
        "",
        str(result.get("next_action")),
        "",
        "## Shared-account stages",
        "",
        "| Stage | Integrity | Geo/day | Return | Trades | Wins | Active days | MDD | Winner share |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    runs = shared.get("runs", {})
    if isinstance(runs, dict):
        for name, run in runs.items():
            if isinstance(run, dict):
                lines.append(
                    "| {name} | {integrity} | {geo} | {ret} | {trades} | {wins} | {active} | {mdd} | {share} |".format(
                        name=name,
                        integrity=run.get("integrity_pass"),
                        geo=run.get("geometric_daily_growth"),
                        ret=run.get("total_return"),
                        trades=run.get("trades"),
                        wins=run.get("wins"),
                        active=run.get("active_days"),
                        mdd=run.get("max_drawdown"),
                        share=run.get("largest_winner_share"),
                    ),
                )
    lines.extend([
        "",
        "Independent symbol-account returns are never added. Every shared stage is one account, one venue and one BacktestNode.",
        "",
    ])
    output.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    btc = subparsers.add_parser("btc")
    btc.add_argument("--root", type=Path, required=True)
    btc.add_argument("--summary", type=Path, required=True)
    btc.add_argument("--winner", type=Path, required=True)
    btc.add_argument("--source-commit", required=True)
    btc.add_argument("--workflow-run-id", required=True)

    combined = subparsers.add_parser("combined")
    combined.add_argument("--btc-summary", type=Path, required=True)
    combined.add_argument("--btc-winner", type=Path, required=True)
    combined.add_argument("--shared-root", type=Path, required=True)
    combined.add_argument("--shared", type=Path, required=True)
    combined.add_argument("--output", type=Path, required=True)
    combined.add_argument("--source-commit", required=True)
    combined.add_argument("--workflow-run-id", required=True)

    args = parser.parse_args()
    if args.command == "btc":
        summary, winner = finalize_btc(
            root=args.root,
            summary_path=args.summary,
            winner_path=args.winner,
            source_commit=args.source_commit,
            workflow_run_id=args.workflow_run_id,
        )
        print(json.dumps({"summary": summary, "winner": winner}, indent=2, sort_keys=True))
    else:
        btc_summary = load(args.btc_summary)
        btc_winner = load(args.btc_winner)
        if btc_summary is None or btc_winner is None:
            raise SystemExit("missing compact BTC evidence")
        shared = finalize_shared(
            root=args.shared_root,
            shared_path=args.shared,
            source_commit=args.source_commit,
            workflow_run_id=args.workflow_run_id,
        )
        result = combine(
            btc_summary=btc_summary,
            btc_winner=btc_winner,
            shared=shared,
            output=args.output,
            source_commit=args.source_commit,
            workflow_run_id=args.workflow_run_id,
        )
        print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
