"""Choose the single failed base stage eligible for candidate-08 family ablation."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import Any

from auction_family_ablation_decision import select_single_family_ablation


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def choose_failed_stage(
    *,
    root: Path,
    first_output: Path,
    screen_output: Path,
    first_passed: bool,
    screen_status: str,
) -> dict[str, Any]:
    first_path = first_output / "suite_metrics.json"
    screen_path = screen_output / "suite_metrics.json"
    summary_path: Path | None = None

    if not first_passed and first_path.exists():
        summary_path = first_path
    elif first_passed and screen_status == "0" and screen_path.exists():
        screen_summary = _load(screen_path)
        if not bool(screen_summary.get("suite_gate_passed")):
            summary_path = screen_path

    if summary_path is None:
        payload: dict[str, Any] = {
            "selected": False,
            "reason": "NO_VALID_FAILED_BASE_STAGE_FOR_ABLATION",
            "suite": "",
            "family_mode": None,
            "retained_family": None,
            "removed_family": None,
            "contributions": [],
            "base_summary_path": None,
            "base_summary_sha256": None,
            "output": None,
        }
    else:
        summary = _load(summary_path)
        decision = select_single_family_ablation(summary)
        payload = {
            **asdict(decision),
            "base_summary_path": str(summary_path),
            "base_summary_sha256": _sha256(summary_path),
        }
        payload["output"] = (
            str(root / f"{decision.suite}-ablation-{decision.family_mode}-v1")
            if decision.selected
            else None
        )
    return payload


def _write_outputs(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(f"selected={'true' if payload['selected'] else 'false'}\n")
        stream.write(f"reason={payload['reason']}\n")
        stream.write(f"suite={payload['suite']}\n")
        stream.write(f"family_mode={payload['family_mode'] or ''}\n")
        stream.write(f"retained_family={payload['retained_family'] or ''}\n")
        stream.write(f"removed_family={payload['removed_family'] or ''}\n")
        stream.write(f"output={payload['output'] or ''}\n")
        stream.write(f"base_summary_sha256={payload['base_summary_sha256'] or ''}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--first-output", type=Path, required=True)
    parser.add_argument("--screen-output", type=Path, required=True)
    parser.add_argument("--first-passed", choices=("true", "false"), required=True)
    parser.add_argument("--screen-status", default="")
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args()

    payload = choose_failed_stage(
        root=args.root,
        first_output=args.first_output,
        screen_output=args.screen_output,
        first_passed=args.first_passed == "true",
        screen_status=args.screen_status,
    )
    args.root.mkdir(parents=True, exist_ok=True)
    (args.root / "ablation_decision.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_outputs(args.github_output, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
