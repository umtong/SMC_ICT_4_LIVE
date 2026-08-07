#!/usr/bin/env python3
"""Materialize the frozen W10-W12 protocol after all source generators run."""
from __future__ import annotations

import json
from pathlib import Path
import re

WEEKS = tuple(f"W{index}" for index in range(1, 13))


def patch_argument(path: Path, include_long: bool) -> None:
    source = path.read_text(encoding="utf-8")
    values = list(WEEKS) + (["LONG"] if include_long else [])
    tuple_text = "(" + ", ".join(repr(value) for value in values) + ")"
    pattern = re.compile(
        r'(parser\.add_argument\("--week",\s*choices=)\([^\n]*?\)(,\s*(?:required=True|default="W1")\))'
    )
    updated, count = pattern.subn(r"\1" + tuple_text + r"\2", source, count=1)
    if count != 1:
        raise SystemExit(f"week parser anchor missing in {path.name}")
    path.write_text(updated, encoding="utf-8")


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
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    patch_argument(root / "run_leadership_scdam.py", include_long=False)
    patch_argument(root / "evidence_audit.py", include_long=True)
    print(json.dumps({"materialized_weeks": protocol["weeks"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
