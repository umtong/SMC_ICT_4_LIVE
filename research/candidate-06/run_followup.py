"""Continue candidate-06 from committed unified-campaign evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def _run(command: list[str], cwd: Path) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    return {
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-8000:],
        "stderr_tail": completed.stderr[-16000:],
    }


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _screen(candidate_dir: Path, repository: Path, config: Path, output: Path, weeks: int) -> dict[str, Any]:
    process = _run(
        [
            sys.executable,
            str(candidate_dir / "run_long_stitched.py"),
            "--config",
            str(config),
            "--output",
            str(output),
            "--start",
            "2025-01-06",
            "--weeks",
            str(weeks),
        ],
        repository,
    )
    summary = _load(output / "summary.json")
    return {"process": process, "summary": summary, "gate_passed": bool(summary and summary.get("gate_passed"))}


def _continuous(candidate_dir: Path, repository: Path, config: Path, output: Path) -> dict[str, Any]:
    process = _run(
        [
            sys.executable,
            str(candidate_dir / "run_continuous_confirmation.py"),
            "--config",
            str(config),
            "--output",
            str(output),
            "--start",
            "2025-01-01",
            "--end-exclusive",
            "2026-01-01",
        ],
        repository,
    )
    metrics = _load(output / "metrics.json")
    return {"process": process, "metrics": metrics, "gate_passed": bool(metrics and metrics.get("gate_passed"))}


def _render(result: dict[str, Any]) -> str:
    lines = [
        "# Candidate 06 follow-up decision",
        "",
        f"Path: `{result.get('path')}`",
        f"Complete candidate: `{result.get('complete_candidate')}`",
        f"Selected candidate: `{result.get('selected_candidate')}`",
        "",
    ]
    if result.get("matrix_summary"):
        summary = result["matrix_summary"]
        lines.extend(
            [
                "## v0.5 three-week stage",
                "",
                f"- selected variant: `{summary.get('selected')}`",
                f"- all three weeks passed: `{summary.get('all_three_weeks_passed')}`",
                "",
            ]
        )
    for key, title in (("promotion_13w", "13-week promotion"), ("full_52w", "52-week screen")):
        if result.get(key):
            summary = result[key].get("summary") or {}
            lines.extend(
                [
                    f"## {title}",
                    "",
                    f"- gate: `{result[key].get('gate_passed')}`",
                    f"- geometric daily NAV growth: `{summary.get('geometric_daily_nav_growth')}`",
                    f"- ending NAV: `{summary.get('ending_nav')}`",
                    f"- trades/day: `{summary.get('trades_per_day')}`",
                    f"- win rate: `{summary.get('win_rate')}`",
                    f"- maximum drawdown: `{summary.get('max_drawdown_nav')}`",
                    f"- failures: `{summary.get('gate_failures')}`",
                    "",
                ]
            )
    if result.get("continuous"):
        metrics = result["continuous"].get("metrics") or {}
        lines.extend(
            [
                "## Single-engine continuous 2025 confirmation",
                "",
                f"- gate: `{result['continuous'].get('gate_passed')}`",
                f"- geometric daily NAV growth: `{metrics.get('geometric_daily_nav_growth')}`",
                f"- ending NAV: `{metrics.get('ending_nav')}`",
                f"- trades/day: `{metrics.get('trades_per_day')}`",
                f"- win rate: `{metrics.get('win_rate')}`",
                f"- maximum drawdown: `{metrics.get('max_drawdown_nav')}`",
                f"- failures: `{metrics.get('gate_failures')}`",
                f"- error: `{metrics.get('error')}`",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/candidate-06/followup"))
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    campaign_path = repository / "artifacts/candidate-06/campaign/campaign.json"
    campaign = _load(campaign_path)
    result: dict[str, Any] = {
        "campaign_evidence": str(campaign_path.relative_to(repository)),
        "complete_candidate": False,
        "selected_candidate": None,
    }
    if campaign is None:
        result["path"] = "WAITING_FOR_UNIFIED_CAMPAIGN_EVIDENCE"
        (output / "followup.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (output / "FOLLOWUP.md").write_text(_render(result), encoding="utf-8")
        return 4

    if campaign.get("campaign_success") and campaign.get("selected_config"):
        config = repository / str(campaign["selected_config"])
        result["path"] = "CONTINUOUS_CONFIRMATION_OF_UNIFIED_CAMPAIGN_WINNER"
        result["selected_candidate"] = campaign.get("selected_candidate")
        continuous = _continuous(candidate_dir, repository, config, output / "continuous-2025")
        result["continuous"] = continuous
        result["complete_candidate"] = continuous["gate_passed"]
    else:
        result["path"] = "V0_5_SESSION_EQUILIBRIUM_RETEST"
        matrix_output = output / "v0.5-equilibrium-matrix"
        process = _run(
            [sys.executable, str(candidate_dir / "run_equilibrium_matrix.py"), "--output", str(matrix_output)],
            repository,
        )
        result["matrix_process"] = process
        matrix = _load(matrix_output / "summary.json")
        result["matrix_summary"] = matrix
        locked = candidate_dir / "config.equilibrium.locked.json"
        if matrix and matrix.get("all_three_weeks_passed") and locked.exists():
            promotion = _screen(candidate_dir, repository, locked, output / "v0.5-long-13w", 13)
            result["promotion_13w"] = promotion
            if promotion["gate_passed"]:
                full = _screen(candidate_dir, repository, locked, output / "v0.5-long-52w", 52)
                result["full_52w"] = full
                if full["gate_passed"]:
                    result["selected_candidate"] = "session_equilibrium_retest_v5"
                    continuous = _continuous(candidate_dir, repository, locked, output / "continuous-2025")
                    result["continuous"] = continuous
                    result["complete_candidate"] = continuous["gate_passed"]

    (output / "followup.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "FOLLOWUP.md").write_text(_render(result), encoding="utf-8")
    return 0 if result["complete_candidate"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
