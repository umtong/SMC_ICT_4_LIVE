#!/usr/bin/env python3
"""Materialize the frozen L1 interval after source generators run."""
from __future__ import annotations

import json
from pathlib import Path
import re


def patch_runner(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r'(parser\.add_argument\("--week",\s*choices=)\(([^\n]*?)\)(,\s*default="W1"\))'
    )
    match = pattern.search(source)
    if match is None:
        raise SystemExit("Nautilus runner week parser anchor missing")
    values = [value.strip().strip("'\"") for value in match.group(2).split(",") if value.strip()]
    if "L1" not in values:
        values.append("L1")
    replacement = match.group(1) + "(" + ", ".join(repr(value) for value in values) + ")" + match.group(3)
    path.write_text(source[:match.start()] + replacement + source[match.end():], encoding="utf-8")


def main() -> None:
    root = Path(__file__).resolve().parent
    protocol_path = root / "irx_long_protocol.json"
    if not protocol_path.is_file():
        raise SystemExit("frozen IRX long protocol is missing")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if protocol.get("seed") != 2026080813:
        raise SystemExit("unexpected IRX long protocol seed")
    interval = protocol.get("interval")
    if not isinstance(interval, dict) or set(interval) != {"start", "end_exclusive"}:
        raise SystemExit("invalid IRX long interval")
    config_path = root / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    weeks = config.setdefault("selection", {}).setdefault("weeks", {})
    if "L1" in weeks and weeks["L1"] != interval:
        raise SystemExit("frozen L1 interval changed")
    weeks["L1"] = interval
    config["selection"]["evaluation_days"] = 90
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    patch_runner(root / "run_leadership_scdam.py")
    print(json.dumps({"L1": interval}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
