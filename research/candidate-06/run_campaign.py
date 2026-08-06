"""Unified staged candidate-06 research campaign.

The campaign is intentionally sequential.  Each causal family must pass its own
pure state tests, the first frozen BTC week, and two frozen follow-up weeks before
it is eligible for a 2025 long screen.  A 13-week promotion screen precedes the
full 52-week screen.  All PnL-producing subprocesses call ``run_validation.py``
and therefore NautilusTrader; this coordinator contains no fill or PnL engine.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


CANDIDATES: tuple[dict[str, str], ...] = (
    {
        "name": "lrb_post_sweep_v2",
        "test": "test_logic_v2.py",
        "matrix": "run_matrix.py",
        "summary": "matrix_summary.json",
        "locked": "config.locked.json",
    },
    {
        "name": "session_liquidity_transfer_v3",
        "test": "test_session_engine.py",
        "matrix": "run_session_matrix.py",
        "summary": "summary.json",
        "locked": "config.session.locked.json",
    },
    {
        "name": "session_displacement_retest_v4",
        "test": "test_session_displacement_engine.py",
        "matrix": "run_displacement_matrix.py",
        "summary": "summary.json",
        "locked": "config.displacement.locked.json",
    },
)


def _subprocess(command: list[str], cwd: Path) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    return {
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-8000:],
        "stderr_tail": completed.stderr[-16000:],
    }


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"parse_error": f"{type(exc).__name__}: {exc}"}


def _long_screen(
    *,
    candidate_dir: Path,
    repository: Path,
    config_path: Path,
    output: Path,
    weeks: int,
) -> dict[str, Any]:
    process = _subprocess(
        [
            sys.executable,
            str(candidate_dir / "run_long_stitched.py"),
            "--config",
            str(config_path),
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
    return {
        "process": process,
        "summary": summary,
        "gate_passed": bool(summary and summary.get("gate_passed")),
    }


def _render(campaign: dict[str, Any]) -> str:
    lines = [
        "# Candidate 06 unified research campaign",
        "",
        "Candidate families are evaluated in the committed order. Maximum return is never the selector.",
        "",
        f"Campaign success: `{campaign['campaign_success']}`",
        f"Selected candidate: `{campaign.get('selected_candidate')}`",
        f"Continuous confirmation authorized: `{campaign.get('continuous_confirmation_authorized')}`",
        "",
    ]
    for record in campaign["candidate_results"]:
        lines.extend(
            [
                f"## {record['name']}",
                "",
                f"- pure state test return code: `{record['test_process']['returncode']}`",
                f"- matrix return code: `{record.get('matrix_process', {}).get('returncode')}`",
                f"- all three frozen weeks passed: `{record.get('all_three_weeks_passed')}`",
                f"- selected variant: `{record.get('selected_variant')}`",
            ]
        )
        if record.get("promotion_13w"):
            summary = record["promotion_13w"].get("summary") or {}
            lines.extend(
                [
                    f"- 13-week gate: `{record['promotion_13w'].get('gate_passed')}`",
                    f"- 13-week geometric daily NAV growth: `{summary.get('geometric_daily_nav_growth')}`",
                    f"- 13-week trades/day: `{summary.get('trades_per_day')}`",
                    f"- 13-week win rate: `{summary.get('win_rate')}`",
                    f"- 13-week max drawdown: `{summary.get('max_drawdown_nav')}`",
                    f"- 13-week failures: `{summary.get('gate_failures')}`",
                ]
            )
        if record.get("full_52w"):
            summary = record["full_52w"].get("summary") or {}
            lines.extend(
                [
                    f"- 52-week gate: `{record['full_52w'].get('gate_passed')}`",
                    f"- 52-week geometric daily NAV growth: `{summary.get('geometric_daily_nav_growth')}`",
                    f"- 52-week ending NAV: `{summary.get('ending_nav')}`",
                    f"- 52-week trades/day: `{summary.get('trades_per_day')}`",
                    f"- 52-week win rate: `{summary.get('win_rate')}`",
                    f"- 52-week max drawdown: `{summary.get('max_drawdown_nav')}`",
                    f"- 52-week failures: `{summary.get('gate_failures')}`",
                ]
            )
        lines.append("")
    if not campaign["campaign_success"]:
        lines.extend(
            [
                "## Decision",
                "",
                "No candidate is promoted as complete. The committed diagnostics identify whether failure arose in state implementation, first-week alpha density, frozen-week transfer, or long-period persistence.",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/candidate-06/campaign"))
    args = parser.parse_args()

    candidate_dir = Path(__file__).resolve().parent
    repository = candidate_dir.parent.parent
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    candidate_results: list[dict[str, Any]] = []
    selected_candidate: str | None = None
    selected_config: str | None = None
    for spec in CANDIDATES:
        record: dict[str, Any] = {"name": spec["name"]}
        test_process = _subprocess([sys.executable, str(candidate_dir / spec["test"])], repository)
        record["test_process"] = test_process
        if test_process["returncode"] != 0:
            record["decision"] = "IMPLEMENTATION_TEST_FAILED"
            candidate_results.append(record)
            continue

        matrix_output = output / spec["name"] / "matrix"
        matrix_process = _subprocess(
            [sys.executable, str(candidate_dir / spec["matrix"]), "--output", str(matrix_output)],
            repository,
        )
        record["matrix_process"] = matrix_process
        matrix_summary = _load(matrix_output / spec["summary"])
        record["matrix_summary"] = matrix_summary
        all_three = bool(matrix_summary and matrix_summary.get("all_three_weeks_passed"))
        record["all_three_weeks_passed"] = all_three
        record["selected_variant"] = None if not matrix_summary else matrix_summary.get("selected")
        locked_path = candidate_dir / spec["locked"]
        record["locked_config"] = str(locked_path.relative_to(repository)) if locked_path.exists() else None
        if not all_three or not locked_path.exists():
            record["decision"] = "NOT_PROMOTED_FROM_THREE_WEEK_STAGE"
            candidate_results.append(record)
            continue

        promotion = _long_screen(
            candidate_dir=candidate_dir,
            repository=repository,
            config_path=locked_path,
            output=output / spec["name"] / "long-13w",
            weeks=13,
        )
        record["promotion_13w"] = promotion
        if not promotion["gate_passed"]:
            record["decision"] = "FAILED_13_WEEK_PROMOTION"
            candidate_results.append(record)
            continue

        full = _long_screen(
            candidate_dir=candidate_dir,
            repository=repository,
            config_path=locked_path,
            output=output / spec["name"] / "long-52w",
            weeks=52,
        )
        record["full_52w"] = full
        if not full["gate_passed"]:
            record["decision"] = "FAILED_52_WEEK_LONG_SCREEN"
            candidate_results.append(record)
            continue

        record["decision"] = "PROMOTED_TO_CONTINUOUS_CONFIRMATION"
        selected_candidate = spec["name"]
        selected_config = str(locked_path.relative_to(repository))
        candidate_results.append(record)
        break

    campaign_success = selected_candidate is not None
    campaign = {
        "design": "fixed causal family priority, pure tests, three frozen weeks, 13-week then 52-week Nautilus screens",
        "candidate_priority": [spec["name"] for spec in CANDIDATES],
        "campaign_success": campaign_success,
        "selected_candidate": selected_candidate,
        "selected_config": selected_config,
        "continuous_confirmation_authorized": campaign_success,
        "candidate_results": candidate_results,
    }
    (output / "campaign.json").write_text(json.dumps(campaign, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "CAMPAIGN.md").write_text(_render(campaign), encoding="utf-8")
    return 0 if campaign_success else 2


if __name__ == "__main__":
    raise SystemExit(main())
