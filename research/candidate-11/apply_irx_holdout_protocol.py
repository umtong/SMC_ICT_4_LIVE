#!/usr/bin/env python3
"""Materialize the already-frozen W10-W12 protocol into diagnostic runners."""
from __future__ import annotations

import json
from pathlib import Path
import re

WEEKS = tuple(f"W{index}" for index in range(1, 13))


def patch_choices(source: str, label: str) -> str:
    pattern = re.compile(r'choices=\((?:"W\d+",?\s*)+\)')
    replacement = "choices=(" + ", ".join(f'\"{week}\"' for week in WEEKS) + ")"
    updated, count = pattern.subn(replacement, source)
    if count < 1:
        raise SystemExit(f"{label}: week-choice anchor missing")
    return updated


def main() -> None:
    root = Path(__file__).resolve().parent
    protocol_path = root / "irx_holdout_protocol.json"
    if not protocol_path.is_file():
        raise SystemExit("frozen IRX holdout protocol is missing")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if protocol.get("seed") != 2026080811 or set(protocol.get("weeks", {})) != {"W10", "W11", "W12"}:
        raise SystemExit("unexpected IRX holdout protocol")

    config_path = root / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    weeks = config.setdefault("selection", {}).setdefault("weeks", {})
    for week, interval in protocol["weeks"].items():
        if week in weeks and weeks[week] != interval:
            raise SystemExit(f"frozen {week} interval changed")
        weeks[week] = interval
    rendered = json.dumps(config, indent=2, ensure_ascii=False) + "\n"
    config_path.write_text(rendered, encoding="utf-8")

    runner_path = root / "run_leadership_scdam.py"
    runner_path.write_text(
        patch_choices(runner_path.read_text(encoding="utf-8"), "Nautilus runner"),
        encoding="utf-8",
    )
    audit_path = root / "evidence_audit.py"
    audit_path.write_text(
        patch_choices(audit_path.read_text(encoding="utf-8"), "evidence audit"),
        encoding="utf-8",
    )
    print(json.dumps({"materialized_weeks": protocol["weeks"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
