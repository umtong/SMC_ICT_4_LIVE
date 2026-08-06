"""Run v0.6 only when earlier committed stages did not produce a complete candidate."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def _run(command: list[str], cwd: Path) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    return {"returncode": completed.returncode, "stdout_tail": completed.stdout[-8000:], "stderr_tail": completed.stderr[-16000:]}


def _load(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _screen(candidate_dir: Path, repository: Path, config: Path, output: Path, weeks: int) -> dict[str, Any]:
    process = _run(
        [sys.executable, str(candidate_dir / "run_long_stitched.py"), "--config", str(config), "--output", str(output), "--start", "2025-01-06", "--weeks", str(weeks)],
        repository,
    )
    summary = _load(output / "summary.json")
    return {"process": process, "summary": summary, "gate_passed": bool(summary and summary.get("gate_passed"))}


def _continuous(candidate_dir: Path, repository: Path, config: Path, output: Path) -> dict[str, Any]:
    process = _run(
        [sys.executable, str(candidate_dir / "run_continuous_confirmation.py"), "--config", str(config), "--output", str(output), "--start", "2025-01-01", "--end-exclusive", "2026-01-01"],
        repository,
    )
    metrics = _load(output / "metrics.json")
    return {"process": process, "metrics": metrics, "gate_passed": bool(metrics and metrics.get("gate_passed"))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/candidate-06/breakthrough"))
    args = parser.parse_args()
    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    followup_path = repository / "artifacts/candidate-06/followup/followup.json"
    followup = _load(followup_path)
    result: dict[str, Any] = {
        "path": "WAITING_FOR_FOLLOWUP_EVIDENCE",
        "complete_candidate": False,
        "selected_candidate": None,
    }
    if followup is None:
        pass
    elif followup.get("complete_candidate"):
        result.update(
            {
                "path": "EARLIER_CANDIDATE_ALREADY_COMPLETE",
                "complete_candidate": True,
                "selected_candidate": followup.get("selected_candidate"),
                "inherited_followup": followup,
            }
        )
    else:
        result["path"] = "V0_6_SESSION_TO_SESSION_LIQUIDITY_RELAY"
        test = _run([sys.executable, str(candidate_dir / "test_session_relay_engine.py")], repository)
        result["test_process"] = test
        if test["returncode"] == 0:
            matrix_output = output / "v0.6-relay-matrix"
            matrix_process = _run([sys.executable, str(candidate_dir / "run_relay_matrix.py"), "--output", str(matrix_output)], repository)
            result["matrix_process"] = matrix_process
            matrix = _load(matrix_output / "summary.json")
            result["matrix_summary"] = matrix
            locked = candidate_dir / "config.relay.locked.json"
            if matrix and matrix.get("all_three_weeks_passed") and locked.exists():
                promotion = _screen(candidate_dir, repository, locked, output / "v0.6-long-13w", 13)
                result["promotion_13w"] = promotion
                if promotion["gate_passed"]:
                    full = _screen(candidate_dir, repository, locked, output / "v0.6-long-52w", 52)
                    result["full_52w"] = full
                    if full["gate_passed"]:
                        continuous = _continuous(candidate_dir, repository, locked, output / "continuous-2025")
                        result["continuous"] = continuous
                        result["selected_candidate"] = "session_liquidity_relay_v6"
                        result["complete_candidate"] = continuous["gate_passed"]

    (output / "breakthrough.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Candidate 06 breakthrough continuation",
        "",
        f"- path: `{result.get('path')}`",
        f"- complete candidate: `{result.get('complete_candidate')}`",
        f"- selected candidate: `{result.get('selected_candidate')}`",
        "",
    ]
    matrix = result.get("matrix_summary") or {}
    if matrix:
        lines.extend([f"- v0.6 selected variant: `{matrix.get('selected')}`", f"- all three frozen weeks: `{matrix.get('all_three_weeks_passed')}`", ""])
    for key, title in (("promotion_13w", "13-week promotion"), ("full_52w", "52-week long screen")):
        if result.get(key):
            summary = result[key].get("summary") or {}
            lines.extend(
                [
                    f"## {title}",
                    f"- gate: `{result[key].get('gate_passed')}`",
                    f"- geometric daily NAV growth: `{summary.get('geometric_daily_nav_growth')}`",
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
                "## Continuous 2025 confirmation",
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
    (output / "BREAKTHROUGH.md").write_text("\n".join(lines), encoding="utf-8")
    return 0 if result["complete_candidate"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
